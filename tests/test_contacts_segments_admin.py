"""
Tests for the server-rendered (HTMX) Contacts + Segments admin pages (producer A2):

  GET  /<org_slug>/contacts              → contacts_page
  GET  /<org_slug>/contacts/<id>         → contact_profile_page
  POST /<org_slug>/contacts/<id>/delete  → contact_delete
  GET  /<org_slug>/segments              → segments_page
  GET  /<org_slug>/segments/new          → segment_builder (new)
  POST /<org_slug>/segments/new          → segment_builder (create)
  GET  /<org_slug>/segments/<id>         → segment_builder (edit)
  POST /<org_slug>/segments/preview      → segment_preview

Hits the pages through the REAL TenantSlugMiddleware path (slug resolves + activates the org
schema) with Django's session test ``Client`` + ``force_login`` (these are Django HTML views
gated by ``@login_required`` + ``can()`` — not DRF). Org creation provisions a Postgres schema
(DDL) → ``transactional_db`` + ``force_drop`` cleanup, mirroring tests/test_builder_host_page.py.

Coverage (the brief's verify block):
  - 200 + real content for the contacts list, contact profile, segments list, and builder GET.
  - RBAC: non-member → 403 (list page) / 404 (contact, no oracle); workspace ``read`` member
    can VIEW but is 403 on the delete POST and on the builder edit (read < write).
  - Segment create via POST → a real Segment row exists; preview POST returns a count + sample.
  - Empty environment (member, no workspace access yet) → the page renders 200 with empty-state.
  - The pages WIRE TO THE SAME services/models the API uses (created via those models here).
"""
import json
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.contacts.models import Contact, ContactAttributeKey
from apps.contacts.services import upsert_contact
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.responses.models import Response
from apps.segments.models import Segment
from apps.segments.services import create_segment
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "cs-admin-org"


# --------------------------------------------------------------------------- #
#  Fixtures / helpers                                                          #
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
    """Org + owner + workspace + prod env + attribute keys + a few contacts + a segment + a
    response. Yields a dict of the built objects (tenant schema active)."""
    owner = _mk_user("cs_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="CS Admin Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)

    # create_organization now auto-provisions a "Default workspace" (+ prod/dev envs). These
    # tests exercise a single controlled workspace, so drop the default and use our own — the
    # admin pages resolve to a single deterministic environment this way.
    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)

    # Typed attribute keys (email + plan + company). email is PII.
    ContactAttributeKey.objects.create(environment=env, key="email", data_type="string", is_pii=True, label="Email")
    ContactAttributeKey.objects.create(environment=env, key="name", data_type="string", label="Name")
    ContactAttributeKey.objects.create(environment=env, key="plan", data_type="string", label="Plan")
    ContactAttributeKey.objects.create(environment=env, key="company", data_type="string", label="Company")

    # Two contacts via the SAME service the API uses (dedup-by-email upsert).
    c1, _ = upsert_contact(
        environment=env,
        attributes=[
            {"key": "email", "value": "elena@techflow.io"},
            {"key": "name", "value": "Elena Kravchenko"},
            {"key": "plan", "value": "Enterprise"},
            {"key": "company", "value": "TechFlow Inc."},
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

    # A segment (Enterprise plan) via the create service.
    seg = create_segment(
        environment=env,
        title="Enterprise customers",
        filter_def={"attributeKey": "plan", "operator": "equals", "value": "Enterprise"},
    )

    # A finished response attributed to c1 (drives the profile responses tab + last-seen).
    survey = Survey.objects.create(
        environment=env, name="NPS Survey", status=SurveyStatus.DRAFT,
        questions=[{"id": "q1", "type": "openText", "headline": {"default": "Hi"}}],
        languages=[{"code": "en", "label": "English", "default": True}],
    )
    Response.objects.create(survey=survey, contact_id=c1.id, finished=True, data={"q1": "yes"})

    yield {"org": org, "owner": owner, "ws": ws, "env": env, "c1": c1, "c2": c2, "seg": seg, "survey": survey}
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
#  Contacts list                                                               #
# --------------------------------------------------------------------------- #
def test_contacts_list_renders_for_owner(client, ctx):
    """Owner (implicit manage) → 200 with real contact rows, attribute columns, segment rail."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    # Real contacts + their (unmasked, since owner is write+) email.
    assert "Elena Kravchenko" in html
    assert "elena@techflow.io" in html
    assert "Marcus Johnson" in html
    # The saved-segments rail shows the real segment + its matching count (1 Enterprise contact).
    assert "Enterprise customers" in html
    # Attribute column (plan/company) appears.
    assert "Plan" in html or "Company" in html
    # Active nav + shell.
    assert "is-active" in html
    assert 'id="opensurvey-builder"' not in html  # not the React island page


def test_contacts_list_search_filters(client, ctx):
    """?q= filters by attribute value (case-insensitive contains) — wired to the model query."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts?q=techflow")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Elena Kravchenko" in html
    assert "Marcus Johnson" not in html


def test_contacts_list_masks_pii_for_read_only(client, ctx):
    """A workspace ``read`` member sees the page (200) but PII email values are masked."""
    reader = _add_member(ctx["org"], "cs_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(f"/{SLUG}/contacts")
    assert resp.status_code == 200
    html = resp.content.decode()
    # Email is is_pii=True; read-only callers must NOT see the raw value.
    assert "elena@techflow.io" not in html
    assert "marcus@retailplus.com" not in html


def test_contacts_list_non_member_forbidden(client, ctx):
    """A non-member of the org → 403 on the contacts page (mirrors members_page)."""
    outsider = _mk_user("cs_outsider@example.com")
    client.force_login(outsider)
    resp = client.get(f"/{SLUG}/contacts")
    assert resp.status_code == 403


def test_contacts_list_anonymous_redirects(client, ctx):
    """Anonymous → @login_required redirects to LOGIN_URL."""
    resp = client.get(f"/{SLUG}/contacts")
    assert resp.status_code in (301, 302)


def test_contacts_list_empty_environment_member(client, ctx):
    """An org member with NO workspace access has no readable env → 200 + empty-state (not 403)."""
    member = _add_member(ctx["org"], "cs_noenv@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/contacts")
    assert resp.status_code == 200
    assert b"No contacts yet" in resp.content


# --------------------------------------------------------------------------- #
#  Contact profile                                                             #
# --------------------------------------------------------------------------- #
def test_contact_profile_renders(client, ctx):
    """Owner → 200 with the identity card (attributes), segment-membership pills, responses."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts/{ctx['c1'].id}")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "Elena Kravchenko" in html
    # Attribute key→value (Plan = Enterprise) shown in the card.
    assert "Enterprise" in html
    # The contact matches the "Enterprise customers" segment → membership pill.
    assert "Enterprise customers" in html
    # The responses tab shows the survey this contact answered.
    assert "NPS Survey" in html


def test_contact_profile_unknown_404(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/contacts/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_contact_profile_no_access_404(client, ctx):
    """A member with NO workspace access cannot see the contact → 404 (no existence oracle)."""
    member = _add_member(ctx["org"], "cs_prof_noaccess@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/contacts/{ctx['c1'].id}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
#  Contact delete (bulk-bar / profile)                                         #
# --------------------------------------------------------------------------- #
def test_contact_delete_by_owner(client, ctx):
    """Owner POST delete → the contact is removed and its response anonymized (GDPR service)."""
    client.force_login(ctx["owner"])
    cid = ctx["c2"].id
    resp = client.post(f"/{SLUG}/contacts/{cid}/delete")
    assert resp.status_code in (301, 302)  # redirect to the list
    connection.set_tenant(ctx["org"])
    assert not Contact.objects.filter(pk=cid).exists()


def test_contact_delete_read_only_forbidden(client, ctx):
    """A workspace ``read`` member can VIEW the contact but the delete POST is 403 (read<write)."""
    reader = _add_member(ctx["org"], "cs_del_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(f"/{SLUG}/contacts/{ctx['c1'].id}/delete")
    assert resp.status_code == 403
    connection.set_tenant(ctx["org"])
    assert Contact.objects.filter(pk=ctx["c1"].id).exists()  # not deleted


# --------------------------------------------------------------------------- #
#  Segments list + builder                                                     #
# --------------------------------------------------------------------------- #
def test_segments_list_renders(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/segments")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "Enterprise customers" in html
    assert "New segment" in html
    assert "is-active" in html


def test_segment_builder_new_renders(client, ctx):
    """The new-segment builder GET → 200 with the attribute keys + operator data embedded."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/segments/new")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "New segment" in html
    assert 'id="sb-keys"' in html        # attribute-keys json_script
    assert 'id="sb-ops"' in html         # operators json_script
    assert "segmentBuilder()" in html
    # The hidden env field carries the resolved environment for save/preview.
    assert f'value="{ctx["env"].id}"' in html


def test_segment_builder_edit_renders(client, ctx):
    """The edit builder GET → 200 and hydrates from the saved filter."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/segments/{ctx['seg'].id}")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "Edit segment" in html
    assert "Enterprise customers" in html  # the title pre-filled


def test_segment_builder_edit_read_only_forbidden(client, ctx):
    """A workspace ``read`` member can view segments but editing one is 403 (read<write)."""
    reader = _add_member(ctx["org"], "cs_seg_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(f"/{SLUG}/segments/{ctx['seg'].id}")
    assert resp.status_code == 403


def test_segment_create_via_post(client, ctx):
    """POST the builder form → a real Segment row is created via the create service."""
    client.force_login(ctx["owner"])
    filter_def = {"attributeKey": "plan", "operator": "equals", "value": "Pro"}
    resp = client.post(
        f"/{SLUG}/segments/new",
        {
            "title": "Pro users",
            "filter": json.dumps(filter_def),
            "environment_id": str(ctx["env"].id),
        },
    )
    assert resp.status_code in (301, 302)  # redirect to the segments list
    connection.set_tenant(ctx["org"])
    seg = Segment.objects.get(title="Pro users", environment=ctx["env"])
    assert seg.filter == filter_def


def test_segment_create_invalid_filter_rerenders(client, ctx):
    """An invalid filter (unknown attribute) re-renders the builder with the error, no row made."""
    client.force_login(ctx["owner"])
    bad = {"attributeKey": "nonexistent_key", "operator": "equals", "value": "x"}
    resp = client.post(
        f"/{SLUG}/segments/new",
        {"title": "Bad seg", "filter": json.dumps(bad), "environment_id": str(ctx["env"].id)},
    )
    assert resp.status_code == 200  # re-rendered (not a redirect)
    assert b"unknown attribute" in resp.content.lower()
    connection.set_tenant(ctx["org"])
    assert not Segment.objects.filter(title="Bad seg").exists()


def test_segment_create_read_only_forbidden(client, ctx):
    """A workspace ``read`` member cannot create a segment → 403 on POST."""
    reader = _add_member(ctx["org"], "cs_seg_create_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(
        f"/{SLUG}/segments/new",
        {"title": "Nope", "filter": json.dumps({}), "environment_id": str(ctx["env"].id)},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
#  Segment preview (HTMX live count + sample)                                  #
# --------------------------------------------------------------------------- #
def test_segment_preview_returns_count_and_sample(client, ctx):
    """POST the preview endpoint with a filter → the partial with the matching count + sample.

    The Enterprise filter matches exactly one seeded contact (Elena)."""
    client.force_login(ctx["owner"])
    filter_def = {"attributeKey": "plan", "operator": "equals", "value": "Enterprise"}
    resp = client.post(
        f"/{SLUG}/segments/preview",
        {"filter": json.dumps(filter_def), "environment_id": str(ctx["env"].id)},
    )
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "1 contact" in html  # "≈ 1 contact matches"
    assert "Elena Kravchenko" in html  # sample match


def test_segment_preview_empty_filter_matches_all(client, ctx):
    """An empty filter ({}) previews ALL contacts in the env (match-all sentinel)."""
    client.force_login(ctx["owner"])
    resp = client.post(
        f"/{SLUG}/segments/preview",
        {"filter": "{}", "environment_id": str(ctx["env"].id)},
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "2 contact" in html  # both seeded contacts match


def test_segment_preview_invalid_filter_shows_errors(client, ctx):
    """A semantically invalid filter → the partial with validation messages, never a 500."""
    client.force_login(ctx["owner"])
    bad = {"attributeKey": "plan", "operator": "greaterThan", "value": "5"}  # greaterThan invalid on string
    resp = client.post(
        f"/{SLUG}/segments/preview",
        {"filter": json.dumps(bad), "environment_id": str(ctx["env"].id)},
    )
    assert resp.status_code == 200
    assert b"not valid" in resp.content.lower() or b"refine" in resp.content.lower()
