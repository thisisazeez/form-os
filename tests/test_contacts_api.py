"""
HTTP-level tests for the Contacts API (contracts/openapi.yaml — Contacts tag).

Exercises the real request path (TenantSlugMiddleware → IsOrgMember → can()). Org creation
provisions a Postgres schema, so tests use ``transactional_db`` + ``force_drop=True``. Auth
uses DRF ``force_authenticate``.

Covers: contact create/list(paginated)/detail/patch/delete; typed-attribute validation
(number/boolean/date); auto-create of unknown keys as string; dedup-by-email UPSERT (200 vs
201, case-insensitive value, attribute merge); GDPR delete anonymizes responses (NULLs
contact_id, keeps the row); PII redaction for read-only callers; RBAC gating (read can list
not create; write can create; non-member 404); attribute-key management endpoints.
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from apps.contacts.enums import AttrType
from apps.contacts.models import Contact, ContactAttribute, ContactAttributeKey
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "contacts-api-org"


@pytest.fixture
def api():
    return APIClient()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(
        email=email, password="x", name=email.split("@")[0], is_verified=True
    )


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    """Owner + org + a workspace's production environment. Yields (org, owner, env)."""
    owner = _mk_user("contacts_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Contacts API Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-contacts")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


def _grant_workspace(org, user, env, access):
    """Put user in a fresh team and grant it `access` on env's workspace."""
    connection.set_tenant(org)
    team = Team.objects.create(name=f"team-{uuid.uuid4().hex[:6]}")
    TeamMembership.objects.create(team=team, user_id=user.id)
    WorkspaceTeam.objects.create(team=team, workspace=env.workspace, access=access)


# ---------------------------------------------------------------------------
# create + typed attributes + auto-create keys
# ---------------------------------------------------------------------------
def test_create_contact_with_attributes(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {
            "external_id": "crm-100",
            "attributes": [
                {"key": "email", "value": "alice@example.com"},
                {"key": "plan", "value": "pro"},
            ],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["external_id"] == "crm-100"
    assert set(body) == {"id", "environment_id", "external_id", "attributes", "created_at"}
    attrs = {a["key"]: a for a in body["attributes"]}
    assert attrs["email"]["value"] == "alice@example.com"
    assert attrs["plan"]["value"] == "pro"
    # Unknown keys auto-created as string (documented default).
    connection.set_tenant(org)
    assert ContactAttributeKey.objects.get(environment=env, key="plan").data_type == AttrType.STRING


def test_create_contact_typed_value_validation(api, org_ctx):
    """A typed key rejects a bad value (number/boolean/date) with 400 + per-key errors."""
    org, owner, env = org_ctx
    connection.set_tenant(org)
    ContactAttributeKey.objects.create(environment=env, key="age", data_type=AttrType.NUMBER)
    ContactAttributeKey.objects.create(environment=env, key="signup", data_type=AttrType.DATE)
    api.force_authenticate(user=owner)

    bad_num = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "age", "value": "not-a-number"}]},
        format="json",
    )
    assert bad_num.status_code == 400
    assert "attributes" in bad_num.json()["errors"]
    assert bad_num.json()["errors"]["attributes"][0]["key"] == "age"

    bad_date = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "signup", "value": "yesterday"}]},
        format="json",
    )
    assert bad_date.status_code == 400

    # Valid typed values stored canonically (boolean normalized).
    connection.set_tenant(org)
    ContactAttributeKey.objects.create(environment=env, key="consent", data_type=AttrType.BOOLEAN)
    good = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {
            "attributes": [
                {"key": "age", "value": "42"},
                {"key": "signup", "value": "2026-03-01"},
                {"key": "consent", "value": "1"},
            ]
        },
        format="json",
    )
    assert good.status_code == 201, good.content
    consent = {a["key"]: a["value"] for a in good.json()["attributes"]}["consent"]
    assert consent == "true"  # normalized from "1"


def test_create_contact_duplicate_keys_in_payload_400(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "plan", "value": "a"}, {"key": "plan", "value": "b"}]},
        format="json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# dedup-by-email upsert
# ---------------------------------------------------------------------------
def test_upsert_by_email_returns_200_and_merges(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    first = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "bob@example.com"}, {"key": "plan", "value": "free"}]},
        format="json",
    )
    assert first.status_code == 201
    cid = first.json()["id"]

    # Same email (different case) → upsert: 200, same contact, merged + updated attributes.
    second = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "BOB@example.com"}, {"key": "plan", "value": "pro"}, {"key": "city", "value": "NYC"}]},
        format="json",
    )
    assert second.status_code == 200, second.content
    assert second.json()["id"] == cid  # same contact (deduped by email, case-insensitive)
    attrs = {a["key"]: a["value"] for a in second.json()["attributes"]}
    assert attrs["plan"] == "pro"   # updated
    assert attrs["city"] == "NYC"   # added
    # The supplied attributes always win on upsert — including `email`: the match is
    # case-INSENSITIVE, but the stored value is overwritten with the newly-supplied casing
    # (the caller sent it explicitly; email is not special-cased among the merged keys).
    assert attrs["email"] == "BOB@example.com"

    connection.set_tenant(org)
    assert Contact.objects.filter(environment=env).count() == 1  # no duplicate contact


def test_distinct_emails_create_distinct_contacts(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    a = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "c1@example.com"}]},
        format="json",
    )
    b = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "c2@example.com"}]},
        format="json",
    )
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


def test_blank_email_does_not_dedup(api, org_ctx):
    """A blank email value is NOT a dedup key — two contacts with empty emails stay distinct."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    a = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": ""}, {"key": "plan", "value": "free"}]},
        format="json",
    )
    b = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": ""}, {"key": "plan", "value": "pro"}]},
        format="json",
    )
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    connection.set_tenant(org)
    assert Contact.objects.filter(environment=env).count() == 2


def test_upsert_by_external_id_when_no_email(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    first = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"external_id": "crm-xyz", "attributes": [{"key": "plan", "value": "free"}]},
        format="json",
    )
    assert first.status_code == 201
    # Same external_id, no email → upsert (200), merge plan.
    second = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"external_id": "crm-xyz", "attributes": [{"key": "plan", "value": "pro"}]},
        format="json",
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert {a["key"]: a["value"] for a in second.json()["attributes"]}["plan"] == "pro"


# ---------------------------------------------------------------------------
# list (paginated) + detail
# ---------------------------------------------------------------------------
def test_list_contacts_paginated(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    for i in range(3):
        api.post(
            f"/api/v1/{SLUG}/environments/{env.id}/contacts",
            {"attributes": [{"key": "email", "value": f"u{i}@example.com"}]},
            format="json",
        )
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/contacts?page_size=2")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"count", "page", "page_size", "results"}
    assert body["count"] == 3
    assert body["page_size"] == 2
    assert len(body["results"]) == 2


def test_get_contact_detail(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "d@example.com"}]},
        format="json",
    ).json()
    resp = api.get(f"/api/v1/{SLUG}/contacts/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_unknown_contact_404(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{SLUG}/contacts/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# patch (merge)
# ---------------------------------------------------------------------------
def test_patch_contact_merges_attributes(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "e@example.com"}, {"key": "plan", "value": "free"}]},
        format="json",
    ).json()
    resp = api.patch(
        f"/api/v1/{SLUG}/contacts/{created['id']}",
        {"attributes": [{"key": "plan", "value": "pro"}, {"key": "city", "value": "LA"}]},
        format="json",
    )
    assert resp.status_code == 200
    attrs = {a["key"]: a["value"] for a in resp.json()["attributes"]}
    assert attrs["plan"] == "pro"
    assert attrs["city"] == "LA"
    assert attrs["email"] == "e@example.com"  # untouched key preserved


# ---------------------------------------------------------------------------
# GDPR delete — anonymize responses, keep the row
# ---------------------------------------------------------------------------
def test_delete_contact_anonymizes_all_references(api, org_ctx):
    """GDPR erasure: deleting a contact NULLs its id on Response, Display, AND SurveyLink
    (all plain UUIDFields, ADR-008) while KEEPING those rows — never cascade-deletes them."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "gdpr@example.com"}]},
        format="json",
    ).json()
    cid = uuid.UUID(created["id"])

    # Create a Survey + Response + Display + SurveyLink all referencing this contact.
    connection.set_tenant(org)
    from apps.responses.models import Display, SurveyLink
    from apps.responses.models import Response as SurveyResponse
    from apps.surveys.models import Survey

    survey = Survey.objects.create(environment=env, name="S", questions=[])
    r = SurveyResponse.objects.create(survey=survey, contact_id=cid, finished=True)
    d = Display.objects.create(survey=survey, contact_id=cid)
    link = SurveyLink.objects.create(survey=survey, token=f"tok-{uuid.uuid4().hex}", contact_id=cid)

    # Delete the contact (GDPR erasure).
    resp = api.delete(f"/api/v1/{SLUG}/contacts/{cid}")
    assert resp.status_code == 204

    connection.set_tenant(org)
    # Contact row gone; all three referencing rows SURVIVE but are anonymized (contact_id NULL).
    assert not Contact.objects.filter(pk=cid).exists()
    r.refresh_from_db()
    d.refresh_from_db()
    link.refresh_from_db()
    assert r.contact_id is None and SurveyResponse.objects.filter(pk=r.id).exists()
    assert d.contact_id is None and Display.objects.filter(pk=d.id).exists()
    assert link.contact_id is None and SurveyLink.objects.filter(pk=link.id).exists()


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------
def test_pii_masked_for_read_only_caller(api, org_ctx):
    org, owner, env = org_ctx
    # Mark email as PII.
    connection.set_tenant(org)
    ContactAttributeKey.objects.create(
        environment=env, key="email", data_type=AttrType.STRING, is_pii=True
    )
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "secret@example.com"}]},
        format="json",
    ).json()
    # Owner (implicit manage = write+) sees raw value.
    assert {a["key"]: a["value"] for a in created["attributes"]}["email"] == "secret@example.com"

    # A read-only member sees the masked value.
    reader = _add_member(org, "pii_reader@example.com")
    _grant_workspace(org, reader, env, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.get(f"/api/v1/{SLUG}/contacts/{created['id']}")
    assert resp.status_code == 200
    masked = {a["key"]: a for a in resp.json()["attributes"]}["email"]
    assert masked["value"] == "***"
    assert masked["is_pii"] is True  # flag still surfaced so UI can render the field


def test_pii_masked_on_list_for_read_only_caller(api, org_ctx):
    """PII masking applies on the LIST endpoint too (same serializer + context as detail)."""
    org, owner, env = org_ctx
    connection.set_tenant(org)
    ContactAttributeKey.objects.create(
        environment=env, key="email", data_type=AttrType.STRING, is_pii=True
    )
    api.force_authenticate(user=owner)
    api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "listsecret@example.com"}]},
        format="json",
    )
    reader = _add_member(org, "pii_list_reader@example.com")
    _grant_workspace(org, reader, env, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/contacts")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    email_attr = {a["key"]: a for a in results[0]["attributes"]}["email"]
    assert email_attr["value"] == "***"
    assert email_attr["is_pii"] is True


def test_pii_raw_for_write_caller(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    ContactAttributeKey.objects.create(
        environment=env, key="email", data_type=AttrType.STRING, is_pii=True
    )
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "raw@example.com"}]},
        format="json",
    ).json()
    writer = _add_member(org, "pii_writer@example.com")
    _grant_workspace(org, writer, env, WorkspaceAccess.WRITE)
    api.force_authenticate(user=writer)
    resp = api.get(f"/api/v1/{SLUG}/contacts/{created['id']}")
    assert {a["key"]: a["value"] for a in resp.json()["attributes"]}["email"] == "raw@example.com"


# ---------------------------------------------------------------------------
# RBAC gating
# ---------------------------------------------------------------------------
def test_read_only_member_can_list_but_not_create(api, org_ctx):
    org, owner, env = org_ctx
    reader = _add_member(org, "ro_member@example.com")
    _grant_workspace(org, reader, env, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    # List → 200.
    assert api.get(f"/api/v1/{SLUG}/environments/{env.id}/contacts").status_code == 200
    # Create → 403.
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "x@example.com"}]},
        format="json",
    )
    assert resp.status_code == 403


def test_write_member_can_create(api, org_ctx):
    org, owner, env = org_ctx
    writer = _add_member(org, "w_member@example.com")
    _grant_workspace(org, writer, env, WorkspaceAccess.WRITE)
    api.force_authenticate(user=writer)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/contacts",
        {"attributes": [{"key": "email", "value": "w@example.com"}]},
        format="json",
    )
    assert resp.status_code == 201


def test_member_without_workspace_access_404_on_list(api, org_ctx):
    """An org member with no team access to the env's workspace cannot even see it (404)."""
    org, owner, env = org_ctx
    nobody = _add_member(org, "nobody@example.com")
    api.force_authenticate(user=nobody)
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/contacts")
    assert resp.status_code == 404


def test_non_member_forbidden(api, org_ctx):
    org, owner, env = org_ctx
    stranger = _mk_user("stranger@example.com")  # no Membership at all
    api.force_authenticate(user=stranger)
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/contacts")
    assert resp.status_code == 403  # IsOrgMember denies before object checks


def test_anonymous_unauthorized(api, org_ctx):
    org, owner, env = org_ctx
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/contacts")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# attribute-key management
# ---------------------------------------------------------------------------
def test_attribute_key_crud(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    # Create a typed key.
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/attribute-keys",
        {"key": "age", "data_type": "number", "label": "Age", "is_pii": False},
        format="json",
    )
    assert created.status_code == 201, created.content
    kid = created.json()["id"]
    assert created.json()["data_type"] == "number"

    # List.
    listed = api.get(f"/api/v1/{SLUG}/environments/{env.id}/attribute-keys")
    assert listed.status_code == 200
    assert any(k["key"] == "age" for k in listed.json())

    # Patch (mark PII).
    patched = api.patch(
        f"/api/v1/{SLUG}/attribute-keys/{kid}", {"is_pii": True}, format="json"
    )
    assert patched.status_code == 200
    assert patched.json()["is_pii"] is True

    # Duplicate key → 409.
    dup = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/attribute-keys",
        {"key": "age", "data_type": "string"},
        format="json",
    )
    assert dup.status_code == 409

    # Delete cascades the key's attribute values.
    connection.set_tenant(org)
    c = Contact.objects.create(environment=env)
    ContactAttribute.objects.create(
        contact=c, attribute_key=ContactAttributeKey.objects.get(pk=kid), value="30"
    )
    delete = api.delete(f"/api/v1/{SLUG}/attribute-keys/{kid}")
    assert delete.status_code == 204
    connection.set_tenant(org)
    assert not ContactAttributeKey.objects.filter(pk=kid).exists()
    assert not ContactAttribute.objects.filter(contact=c).exists()


def test_attribute_key_create_read_only_member_forbidden(api, org_ctx):
    org, owner, env = org_ctx
    reader = _add_member(org, "ak_reader@example.com")
    _grant_workspace(org, reader, env, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/attribute-keys",
        {"key": "x", "data_type": "string"},
        format="json",
    )
    assert resp.status_code == 403
