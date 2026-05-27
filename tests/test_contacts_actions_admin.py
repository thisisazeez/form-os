"""
Tests for the WIRED Contacts admin actions (parity: make every Contacts UI item functional):

  POST /<org_slug>/contacts/new                 → contact_create        (New-contact modal)
  POST /<org_slug>/contacts/<id>/attributes     → contact_add_attribute (profile "Add attribute")
  GET/POST /<org_slug>/contacts/import          → contact_import        (CSV upload → results)
  GET  /<org_slug>/contacts/export.csv          → contact_export        (streamed RBAC/PII CSV)
  GET  /<org_slug>/contacts?filter=key:op:value → contacts_page         (functional "Add filter")
  POST /<org_slug>/contacts/save-as-segment     → contacts_save_as_segment ("Add to segment" pivot)

Same harness as tests/test_contacts_segments_admin.py: REAL TenantSlugMiddleware path, Django
session Client + force_login (these are HTML views gated by @login_required + can(), not DRF),
``transactional_db`` (org creation = schema DDL). Coverage = RBAC (403/404) + happy + error
paths for every endpoint the brief lists, and the documented add-to-segment decision.
"""
from urllib.parse import unquote

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.contacts.models import Contact, ContactAttribute, ContactAttributeKey
from apps.contacts.services import upsert_contact
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.segments.models import Segment
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "cs-actions-org"


# --------------------------------------------------------------------------- #
#  Fixtures / helpers (mirror tests/test_contacts_segments_admin.py)           #
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return Client()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def ctx(transactional_db):
    """Org + owner + workspace + prod env + attribute keys (email is PII) + two contacts."""
    owner = _mk_user("ca_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="CS Actions Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)

    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)

    ContactAttributeKey.objects.create(environment=env, key="email", data_type="string", is_pii=True, label="Email")
    ContactAttributeKey.objects.create(environment=env, key="name", data_type="string", label="Name")
    ContactAttributeKey.objects.create(environment=env, key="plan", data_type="string", label="Plan")

    c1, _ = upsert_contact(
        environment=env,
        attributes=[
            {"key": "email", "value": "elena@techflow.io"},
            {"key": "name", "value": "Elena Kravchenko"},
            {"key": "plan", "value": "Enterprise"},
        ],
    )
    c2, _ = upsert_contact(
        environment=env,
        attributes=[
            {"key": "email", "value": "marcus@retailplus.com"},
            {"key": "name", "value": "Marcus Johnson"},
            {"key": "plan", "value": "Pro"},
        ],
    )

    yield {"org": org, "owner": owner, "ws": ws, "env": env, "c1": c1, "c2": c2}
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(user=user, organization=org, role=role, status=MembershipStatus.ACTIVE)
    return user


def _grant(org, user, workspace_id, access):
    connection.set_tenant(org)
    team = Team.objects.create(name=f"team-{user.email}-{access}")
    TeamMembership.objects.create(team=team, user_id=user.id)
    WorkspaceTeam.objects.create(team=team, workspace_id=workspace_id, access=access)


# --------------------------------------------------------------------------- #
#  Create contact (New-contact modal)                                          #
# --------------------------------------------------------------------------- #
def test_create_contact_happy(client, ctx):
    """Owner POST → a new Contact with email+name attributes is created via upsert_contact."""
    client.force_login(ctx["owner"])
    resp = client.post(
        f"/{SLUG}/contacts/new",
        {"email": "nina@startup.dev", "name": "Nina Park"},
    )
    assert resp.status_code in (301, 302)  # redirect to the new profile
    connection.set_tenant(ctx["org"])
    match = (
        ContactAttribute.objects.filter(
            contact__environment=ctx["env"], attribute_key__key="email", value="nina@startup.dev"
        )
        .select_related("contact")
        .first()
    )
    assert match is not None


def test_create_contact_extra_attributes(client, ctx):
    """attr_key[]/attr_value[] pairs from the modal become attributes on the new contact."""
    client.force_login(ctx["owner"])
    resp = client.post(
        f"/{SLUG}/contacts/new",
        {"email": "owen@acme.test", "name": "Owen Reyes", "attr_key": ["plan"], "attr_value": ["Pro"]},
    )
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    attr = ContactAttribute.objects.filter(
        contact__environment=ctx["env"], attribute_key__key="email", value="owen@acme.test"
    ).select_related("contact").first()
    assert attr is not None
    plan = ContactAttribute.objects.get(contact=attr.contact, attribute_key__key="plan")
    assert plan.value == "Pro"


def test_create_contact_dedup_by_email_upsert(client, ctx):
    """Posting an EXISTING email merges (no duplicate row) — the documented dedup-by-email."""
    client.force_login(ctx["owner"])
    before = Contact.objects.filter(environment=ctx["env"]).count()
    resp = client.post(
        f"/{SLUG}/contacts/new",
        {"email": "Elena@TechFlow.io", "name": "Elena K.", "attr_key": ["plan"], "attr_value": ["Scale"]},
    )
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    after = Contact.objects.filter(environment=ctx["env"]).count()
    assert after == before  # merged, not duplicated
    plan = ContactAttribute.objects.get(contact=ctx["c1"], attribute_key__key="plan")
    assert plan.value == "Scale"  # supplied attribute wins


def test_create_contact_read_only_forbidden(client, ctx):
    """A workspace ``read`` member cannot create (read < write) → 403."""
    reader = _add_member(ctx["org"], "ca_create_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(f"/{SLUG}/contacts/new", {"email": "x@y.z", "name": "X"})
    assert resp.status_code == 403
    connection.set_tenant(ctx["org"])
    assert not ContactAttribute.objects.filter(
        attribute_key__key="email", value="x@y.z"
    ).exists()


def test_create_contact_empty_payload_reopens_modal(client, ctx):
    """No email/name → redirect back with the create modal + an error note (no row created)."""
    client.force_login(ctx["owner"])
    before = Contact.objects.filter(environment=ctx["env"]).count()
    resp = client.post(f"/{SLUG}/contacts/new", {})
    assert resp.status_code in (301, 302)
    assert "new=1" in resp.url and "create_error" in resp.url
    connection.set_tenant(ctx["org"])
    assert Contact.objects.filter(environment=ctx["env"]).count() == before


# --------------------------------------------------------------------------- #
#  Add / edit attribute (profile)                                              #
# --------------------------------------------------------------------------- #
def test_add_attribute_happy(client, ctx):
    """Owner POST sets a new attribute on the contact via update_contact_attributes."""
    client.force_login(ctx["owner"])
    resp = client.post(
        f"/{SLUG}/contacts/{ctx['c2'].id}/attributes",
        {"key": "country", "value": "US"},
    )
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    attr = ContactAttribute.objects.get(contact=ctx["c2"], attribute_key__key="country")
    assert attr.value == "US"
    # The key auto-created as string (documented low-friction default).
    assert ContactAttributeKey.objects.get(environment=ctx["env"], key="country").data_type == "string"


def test_add_attribute_overwrites_existing(client, ctx):
    """Setting an existing key overwrites the value (merge upsert, one value per key)."""
    client.force_login(ctx["owner"])
    resp = client.post(
        f"/{SLUG}/contacts/{ctx['c1'].id}/attributes",
        {"key": "plan", "value": "Scale"},
    )
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    assert ContactAttribute.objects.get(contact=ctx["c1"], attribute_key__key="plan").value == "Scale"


def test_add_attribute_missing_key_errors(client, ctx):
    """An empty key → redirect back with an error note, no attribute written."""
    client.force_login(ctx["owner"])
    resp = client.post(f"/{SLUG}/contacts/{ctx['c1'].id}/attributes", {"key": "", "value": "v"})
    assert resp.status_code in (301, 302)
    assert "attr_error" in resp.url


def test_add_attribute_read_only_forbidden(client, ctx):
    """A workspace ``read`` member cannot edit attributes → 403."""
    reader = _add_member(ctx["org"], "ca_attr_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(
        f"/{SLUG}/contacts/{ctx['c1'].id}/attributes", {"key": "country", "value": "US"}
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
#  CSV import                                                                  #
# --------------------------------------------------------------------------- #
def _csv_file(text, name="contacts.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


def test_import_csv_happy(client, ctx):
    """A valid CSV creates new contacts + updates existing (dedup-by-email) and reports rows."""
    client.force_login(ctx["owner"])
    csv_text = (
        "email,name,plan\n"
        "fresh@new.io,Fresh User,Pro\n"          # new → created
        "elena@techflow.io,Elena Updated,Scale\n"  # existing email → updated
    )
    resp = client.post(f"/{SLUG}/contacts/import", {"file": _csv_file(csv_text)})
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "1 created" in html
    assert "1 updated" in html
    connection.set_tenant(ctx["org"])
    assert ContactAttribute.objects.filter(attribute_key__key="email", value="fresh@new.io").exists()
    # Existing contact merged, not duplicated.
    assert ContactAttribute.objects.get(contact=ctx["c1"], attribute_key__key="name").value == "Elena Updated"


def test_import_csv_row_error_does_not_abort_batch(client, ctx):
    """A row with a bad typed value is reported as an error; the other rows still import."""
    connection.set_tenant(ctx["org"])
    ContactAttributeKey.objects.create(environment=ctx["env"], key="age", data_type="number", label="Age")
    client.force_login(ctx["owner"])
    csv_text = (
        "email,age\n"
        "good@row.io,42\n"          # valid number → created
        "bad@row.io,notanumber\n"   # invalid number → row error
    )
    resp = client.post(f"/{SLUG}/contacts/import", {"file": _csv_file(csv_text)})
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "1 created" in html
    assert "1 error" in html
    connection.set_tenant(ctx["org"])
    # The good row landed; the bad row did NOT create a contact.
    assert ContactAttribute.objects.filter(attribute_key__key="email", value="good@row.io").exists()
    assert not ContactAttribute.objects.filter(attribute_key__key="email", value="bad@row.io").exists()


def test_import_no_file_reports_error(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.post(f"/{SLUG}/contacts/import", {})
    assert resp.status_code == 200
    assert b"No file was uploaded" in resp.content


def test_import_read_only_forbidden(client, ctx):
    """A workspace ``read`` member cannot import → 403."""
    reader = _add_member(ctx["org"], "ca_import_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(f"/{SLUG}/contacts/import", {"file": _csv_file("email\na@b.io\n")})
    assert resp.status_code == 403


def test_import_get_renders_upload_page(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts/import")
    assert resp.status_code == 200
    assert b"Import contacts" in resp.content


# --------------------------------------------------------------------------- #
#  CSV export (PII masking honored)                                            #
# --------------------------------------------------------------------------- #
def _read_streaming(resp) -> str:
    return b"".join(resp.streaming_content).decode("utf-8")


def test_export_owner_sees_raw_pii(client, ctx):
    """Owner (write+) export → raw email values (header includes the attribute keys)."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts/export.csv")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    body = _read_streaming(resp)
    assert "id,external_id,created_at" in body
    assert "email" in body  # column header
    assert "elena@techflow.io" in body  # raw PII for a write+ caller
    assert "marcus@retailplus.com" in body


def test_export_read_only_masks_pii(client, ctx):
    """A workspace ``read`` member export → PII (email) values masked, non-PII shown."""
    reader = _add_member(ctx["org"], "ca_export_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(f"/{SLUG}/contacts/export.csv")
    assert resp.status_code == 200
    body = _read_streaming(resp)
    assert "elena@techflow.io" not in body  # masked
    assert "marcus@retailplus.com" not in body
    assert "***" in body  # PII mask present
    assert "Enterprise" in body  # non-PII plan still exported


def test_export_selected_ids_only(client, ctx):
    """?ids= limits the export to the selected contacts (bulk-bar Export selected)."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts/export.csv?ids={ctx['c1'].id}")
    assert resp.status_code == 200
    body = _read_streaming(resp)
    assert "elena@techflow.io" in body
    assert "marcus@retailplus.com" not in body  # not selected


def test_export_honors_filter_params(client, ctx):
    """?filter= on the export narrows to the same set the filtered list shows (shared path)."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts/export.csv?filter=plan:equals:Enterprise")
    assert resp.status_code == 200
    body = _read_streaming(resp)
    assert "elena@techflow.io" in body
    assert "marcus@retailplus.com" not in body  # filtered out


def test_export_non_member_404(client, ctx):
    outsider = _mk_user("ca_export_outsider@example.com")
    client.force_login(outsider)
    resp = client.get(f"/{SLUG}/contacts/export.csv")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
#  "Add filter" (functional attribute filtering on the list)                   #
# --------------------------------------------------------------------------- #
def test_filter_narrows_list(client, ctx):
    """?filter=plan:equals:Enterprise narrows the table to matching contacts (shared evaluator)."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts?filter=plan:equals:Enterprise")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "Elena Kravchenko" in html
    assert "Marcus Johnson" not in html
    # The active-filter chip renders.
    assert "plan" in html and "equals" in html


def test_filter_unknown_key_ignored(client, ctx):
    """An unknown attribute key in ?filter= is ignored (no 500, all contacts shown)."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts?filter=nope:equals:x")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Elena Kravchenko" in html
    assert "Marcus Johnson" in html


def test_filter_save_as_segment_hidden_input_roundtrips(client, ctx):
    """The active-filter "Save filter as segment" form embeds the composed filter tree as a hidden
    input whose escaped value round-trips back through POST to the builder (proving the chip →
    segment path, not just the bulk-selection path)."""
    import html as _html
    import json as _json
    import re

    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts?filter=plan:equals:Enterprise")
    page = resp.content.decode()
    m = re.search(r'name="filter" value="([^"]*)"', page)
    assert m, "save-as-segment hidden filter input not found"
    raw = _html.unescape(m.group(1))
    tree = _json.loads(raw)
    assert tree == {"attributeKey": "plan", "operator": "equals", "value": "Enterprise"}
    # POST it back exactly as the form would → builder redirect prefilled.
    post = client.post(f"/{SLUG}/contacts/save-as-segment", {"filter": raw})
    assert post.status_code in (301, 302)
    assert "/segments/new" in post.url


# --------------------------------------------------------------------------- #
#  "Add to segment" pivot (save-as-segment)                                    #
# --------------------------------------------------------------------------- #
def test_save_as_segment_from_selection_redirects_to_builder(client, ctx):
    """Bulk "Add to segment" posts contact_ids → redirect to the segment builder prefilled with
    an ``email isOneOf [...]`` filter (the documented dynamic-segment pivot)."""
    client.force_login(ctx["owner"])
    resp = client.post(
        f"/{SLUG}/contacts/save-as-segment",
        {"contact_ids": f"{ctx['c1'].id},{ctx['c2'].id}"},
    )
    assert resp.status_code in (301, 302)
    assert "/segments/new" in resp.url
    decoded = unquote(resp.url)
    assert "isOneOf" in decoded
    assert "elena@techflow.io" in decoded
    assert "marcus@retailplus.com" in decoded


def test_save_as_segment_from_filter_redirects_to_builder(client, ctx):
    """The list "Save filter as segment" posts a filter tree → builder prefilled with it."""
    client.force_login(ctx["owner"])
    resp = client.post(
        f"/{SLUG}/contacts/save-as-segment",
        {"filter": '{"attributeKey": "plan", "operator": "equals", "value": "Enterprise"}'},
    )
    assert resp.status_code in (301, 302)
    assert "/segments/new" in resp.url
    decoded = unquote(resp.url)
    assert "Enterprise" in decoded


def test_save_as_segment_builder_prefilled_renders(client, ctx):
    """Following the redirect, the builder GET hydrates the prefilled filter (sb-initial)."""
    client.force_login(ctx["owner"])
    resp = client.post(
        f"/{SLUG}/contacts/save-as-segment",
        {"filter": '{"attributeKey": "plan", "operator": "equals", "value": "Enterprise"}'},
    )
    follow = client.get(resp.url)
    assert follow.status_code == 200, follow.content
    html = follow.content.decode()
    assert "New segment" in html
    # The prefilled filter is embedded in the sb-initial json_script for Alpine hydration.
    assert "Enterprise" in html


def test_save_as_segment_no_input_bounces_back(client, ctx):
    """No ids/filter → redirect back to contacts with a gentle note (no segment created)."""
    client.force_login(ctx["owner"])
    resp = client.post(f"/{SLUG}/contacts/save-as-segment", {})
    assert resp.status_code in (301, 302)
    assert "/contacts" in resp.url and "segment_error" in resp.url
    connection.set_tenant(ctx["org"])
    assert Segment.objects.filter(environment=ctx["env"]).count() == 0


def test_save_as_segment_read_only_forbidden(client, ctx):
    """A workspace ``read`` member cannot create segments → 403 on the pivot."""
    reader = _add_member(ctx["org"], "ca_seg_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(
        f"/{SLUG}/contacts/save-as-segment", {"contact_ids": str(ctx["c1"].id)}
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
#  Modal wiring (the New-contact ?new=1 opens the Alpine modal)                #
# --------------------------------------------------------------------------- #
def test_new_modal_opens_via_query(client, ctx):
    """?new=1 sets the modal-open flag → the create modal is present + open in the markup."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts?new=1")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "newOpen: true" in html
    assert 'action="contacts/new"' in html


def test_filter_panel_uses_json_script(client, ctx):
    """Add-filter data is embedded via json_script (safe JSON-in-HTML) — NOT an inline |safe blob
    that an apostrophe in a label could break. Pins the segments-builder hydration pattern."""
    connection.set_tenant(ctx["org"])
    # A label with an apostrophe would have broken a single-quoted x-data='{...}' attribute.
    ContactAttributeKey.objects.create(
        environment=ctx["env"], key="owner_plan", data_type="string", label="Owner's plan"
    )
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert '<script id="cf-keys" type="application/json">' in html
    assert '<script id="cf-ops" type="application/json">' in html
    # The page still parses fine with the apostrophe label present (json_script escapes it).
    assert "Owner" in html
