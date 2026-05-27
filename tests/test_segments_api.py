"""
HTTP-level tests for the Segments API + segment evaluation (contracts/openapi.yaml — Segments).

Exercises the real request path (TenantSlugMiddleware → IsOrgMember → can()). Org creation
provisions a Postgres schema, so tests use ``transactional_db`` + ``force_drop=True``.

Covers: segment create/list(filtered)/replace(PUT)/delete; filter validation (structural +
operator/type) → 400 with schema_errors; case-insensitive title uniqueness → 409; segment
EVALUATION (GET /segments/{id}/contacts — matching vs non-matching contacts across attribute
operators, nested AND/OR); unsaved-filter PREVIEW (count + results); RBAC gating (read lists,
write creates/edits, non-member 404).
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
from apps.segments.models import Segment
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "segments-api-org"


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
    owner = _mk_user("segments_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Segments API Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-segments")
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
    connection.set_tenant(org)
    team = Team.objects.create(name=f"team-{uuid.uuid4().hex[:6]}")
    TeamMembership.objects.create(team=team, user_id=user.id)
    WorkspaceTeam.objects.create(team=team, workspace=env.workspace, access=access)


def _seed_attribute_keys(org, env):
    """Declare typed keys used by the filter tests."""
    connection.set_tenant(org)
    ContactAttributeKey.objects.create(environment=env, key="plan", data_type=AttrType.STRING)
    ContactAttributeKey.objects.create(environment=env, key="age", data_type=AttrType.NUMBER)
    ContactAttributeKey.objects.create(environment=env, key="signup", data_type=AttrType.DATE)


def _mk_contact(env, **attrs):
    """Create a contact with {key: value} attributes (keys must already exist). Assumes the
    tenant schema is already active (the org_ctx fixture / _seed_attribute_keys set it)."""
    contact = Contact.objects.create(environment=env)
    for key, value in attrs.items():
        ck = ContactAttributeKey.objects.get(environment=env, key=key)
        ContactAttribute.objects.create(contact=contact, attribute_key=ck, value=value)
    return contact


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def test_create_segment(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {
            "title": "Pro users",
            "environment_id": str(env.id),
            "filter": {"attributeKey": "plan", "operator": "equals", "value": "pro"},
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["title"] == "Pro users"
    assert set(body) == {"id", "environment_id", "title", "filter", "created_at"}
    assert body["filter"]["attributeKey"] == "plan"


def test_create_segment_empty_filter_matches_all(api, org_ctx):
    """An empty filter ({}) is the valid 'all contacts' segment."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Everyone", "environment_id": str(env.id), "filter": {}},
        format="json",
    )
    assert resp.status_code == 201, resp.content


def test_create_segment_invalid_filter_400(api, org_ctx):
    """greaterThan on a string attribute → 400 with schema_errors."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {
            "title": "Bad",
            "environment_id": str(env.id),
            "filter": {"attributeKey": "plan", "operator": "greaterThan", "value": "x"},
        },
        format="json",
    )
    assert resp.status_code == 400, resp.content
    assert "schema_errors" in resp.json()
    assert any("not valid for attribute 'plan'" in m for m in resp.json()["schema_errors"])


def test_create_segment_unknown_attribute_400(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {
            "title": "Ghost",
            "environment_id": str(env.id),
            "filter": {"attributeKey": "ghost", "operator": "equals", "value": "x"},
        },
        format="json",
    )
    assert resp.status_code == 400
    assert any("unknown attribute 'ghost'" in m for m in resp.json()["schema_errors"])


def test_create_segment_structural_error_400(api, org_ctx):
    """A structurally malformed filter (bad connector) → 400 (jsonschema layer)."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {
            "title": "Malformed",
            "environment_id": str(env.id),
            "filter": {"connector": "xor", "conditions": [{"attributeKey": "plan", "operator": "equals", "value": "a"}]},
        },
        format="json",
    )
    assert resp.status_code == 400


def test_create_segment_body_env_mismatch_400(api, org_ctx):
    """A body environment_id that disagrees with the URL is rejected with 400 (no ambiguity
    about where the segment landed)."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Mismatch", "environment_id": str(uuid.uuid4()), "filter": {}},
        format="json",
    )
    assert resp.status_code == 400
    assert "environment_id" in resp.json().get("errors", {})


def test_duplicate_title_409(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    payload = {"title": "VIPs", "environment_id": str(env.id), "filter": {}}
    assert api.post(f"/api/v1/{SLUG}/environments/{env.id}/segments", payload, format="json").status_code == 201
    dup = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "vips", "environment_id": str(env.id), "filter": {}},  # case-insensitive collision
        format="json",
    )
    assert dup.status_code == 409


def test_list_segments(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    for t in ("A", "B"):
        api.post(
            f"/api/v1/{SLUG}/environments/{env.id}/segments",
            {"title": t, "environment_id": str(env.id), "filter": {}},
            format="json",
        )
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/segments")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_replace_segment(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Old", "environment_id": str(env.id), "filter": {}},
        format="json",
    ).json()
    resp = api.put(
        f"/api/v1/{SLUG}/segments/{created['id']}",
        {"title": "New", "filter": {"attributeKey": "age", "operator": "greaterThan", "value": 18}},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["title"] == "New"
    assert resp.json()["filter"]["operator"] == "greaterThan"


def test_replace_segment_invalid_filter_400(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "ToBreak", "environment_id": str(env.id), "filter": {}},
        format="json",
    ).json()
    resp = api.put(
        f"/api/v1/{SLUG}/segments/{created['id']}",
        {"title": "ToBreak", "filter": {"attributeKey": "plan", "operator": "before", "value": "x"}},
        format="json",
    )
    assert resp.status_code == 400


def test_delete_segment(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Temp", "environment_id": str(env.id), "filter": {}},
        format="json",
    ).json()
    resp = api.delete(f"/api/v1/{SLUG}/segments/{created['id']}")
    assert resp.status_code == 204
    connection.set_tenant(org)
    assert not Segment.objects.filter(pk=created["id"]).exists()


# ---------------------------------------------------------------------------
# evaluation — matching vs non-matching
# ---------------------------------------------------------------------------
def test_evaluate_segment_simple(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    # Three contacts: two pro, one free.
    _mk_contact(env, plan="pro", age="30")
    _mk_contact(env, plan="pro", age="50")
    _mk_contact(env, plan="free", age="40")
    api.force_authenticate(user=owner)
    seg = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Pros", "environment_id": str(env.id), "filter": {"attributeKey": "plan", "operator": "equals", "value": "pro"}},
        format="json",
    ).json()
    resp = api.get(f"/api/v1/{SLUG}/segments/{seg['id']}/contacts")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["count"] == 2  # only the two pro contacts match


def test_evaluate_segment_nested_and_or(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    # pro & age>40 → matches c1 only; free is excluded; pro & age=20 excluded.
    c1 = _mk_contact(env, plan="pro", age="60")
    _mk_contact(env, plan="pro", age="20")
    _mk_contact(env, plan="free", age="80")
    api.force_authenticate(user=owner)
    tree = {
        "connector": "and",
        "conditions": [
            {"attributeKey": "plan", "operator": "equals", "value": "pro"},
            {"attributeKey": "age", "operator": "greaterThan", "value": 40},
        ],
    }
    seg = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Pro40+", "environment_id": str(env.id), "filter": tree},
        format="json",
    ).json()
    resp = api.get(f"/api/v1/{SLUG}/segments/{seg['id']}/contacts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["results"][0]["id"] == str(c1.id)


def test_evaluate_segment_is_skipped(api, org_ctx):
    """isSkipped matches contacts MISSING the attribute."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    _mk_contact(env, plan="pro")            # has plan
    c_no_plan = _mk_contact(env, age="30")  # no plan attribute
    api.force_authenticate(user=owner)
    seg = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "NoPlan", "environment_id": str(env.id), "filter": {"attributeKey": "plan", "operator": "isSkipped"}},
        format="json",
    ).json()
    resp = api.get(f"/api/v1/{SLUG}/segments/{seg['id']}/contacts")
    assert resp.json()["count"] == 1
    assert resp.json()["results"][0]["id"] == str(c_no_plan.id)


def test_evaluate_empty_filter_matches_all(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    _mk_contact(env, plan="pro")
    _mk_contact(env, plan="free")
    api.force_authenticate(user=owner)
    seg = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "All", "environment_id": str(env.id), "filter": {}},
        format="json",
    ).json()
    resp = api.get(f"/api/v1/{SLUG}/segments/{seg['id']}/contacts")
    assert resp.json()["count"] == 2


def test_evaluate_is_one_of_empty_array_matches_none(api, org_ctx):
    """isOneOf with an empty value array matches no contacts (documented edge case)."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    _mk_contact(env, plan="pro")
    _mk_contact(env, plan="free")
    api.force_authenticate(user=owner)
    seg = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Nobody", "environment_id": str(env.id), "filter": {"attributeKey": "plan", "operator": "isOneOf", "value": []}},
        format="json",
    ).json()
    resp = api.get(f"/api/v1/{SLUG}/segments/{seg['id']}/contacts")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_evaluate_is_one_of_matches(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    _mk_contact(env, plan="pro")
    _mk_contact(env, plan="ent")
    _mk_contact(env, plan="free")
    api.force_authenticate(user=owner)
    seg = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "PaidTiers", "environment_id": str(env.id), "filter": {"attributeKey": "plan", "operator": "isOneOf", "value": ["pro", "ent"]}},
        format="json",
    ).json()
    resp = api.get(f"/api/v1/{SLUG}/segments/{seg['id']}/contacts")
    assert resp.json()["count"] == 2


def test_evaluate_date_operator(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    _mk_contact(env, signup="2026-01-15")
    _mk_contact(env, signup="2025-06-01")
    api.force_authenticate(user=owner)
    seg = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Recent", "environment_id": str(env.id), "filter": {"attributeKey": "signup", "operator": "after", "value": "2026-01-01"}},
        format="json",
    ).json()
    resp = api.get(f"/api/v1/{SLUG}/segments/{seg['id']}/contacts")
    assert resp.json()["count"] == 1


# ---------------------------------------------------------------------------
# preview (unsaved filter)
# ---------------------------------------------------------------------------
def test_preview_unsaved_filter(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    _mk_contact(env, plan="pro")
    _mk_contact(env, plan="pro")
    _mk_contact(env, plan="free")
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments/preview",
        {"filter": {"attributeKey": "plan", "operator": "equals", "value": "pro"}},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["count"] == 2
    assert len(body["results"]) == 2


def test_preview_invalid_filter_400(api, org_ctx):
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments/preview",
        {"filter": {"attributeKey": "age", "operator": "contains", "value": "x"}},  # contains invalid for number
        format="json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DoS guard — deeply-nested / very-wide filter trees must return 400, NEVER 500
# ---------------------------------------------------------------------------
def _deeply_nested_filter(depth):
    """A filter tree nested `depth` levels deep (a chain of single-child AND groups)."""
    node = {"attributeKey": "plan", "operator": "equals", "value": "x"}
    for _ in range(depth):
        node = {"connector": "and", "conditions": [node]}
    return node


def test_create_segment_overly_deep_filter_400_not_500(api, org_ctx):
    """A filter nested far past MAX_FILTER_DEPTH is rejected with 400 (depth guard) — must NOT
    propagate a RecursionError as a 500 (one-request DoS)."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "TooDeep", "environment_id": str(env.id), "filter": _deeply_nested_filter(2000)},
        format="json",
    )
    assert resp.status_code == 400, resp.content
    assert any("depth" in m for m in resp.json()["schema_errors"])


def test_preview_overly_deep_filter_400_not_500(api, org_ctx):
    """Same DoS guard on the preview endpoint (reachable by any workspace member with read)."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments/preview",
        {"filter": _deeply_nested_filter(2000)},
        format="json",
    )
    assert resp.status_code == 400, resp.content
    assert any("depth" in m for m in resp.json()["schema_errors"])


def test_edit_segment_overly_deep_filter_400_not_500(api, org_ctx):
    """The depth guard also protects PUT (edit_segment)."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "EditDeep", "environment_id": str(env.id), "filter": {}},
        format="json",
    ).json()
    resp = api.put(
        f"/api/v1/{SLUG}/segments/{created['id']}",
        {"title": "EditDeep", "filter": _deeply_nested_filter(2000)},
        format="json",
    )
    assert resp.status_code == 400, resp.content


def test_create_segment_too_many_nodes_400(api, org_ctx):
    """A very WIDE tree (huge node count) is also rejected with 400 (node-count guard)."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    api.force_authenticate(user=owner)
    wide = {
        "connector": "or",
        "conditions": [
            {"attributeKey": "plan", "operator": "equals", "value": str(i)}
            for i in range(2000)
        ],
    }
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "TooWide", "environment_id": str(env.id), "filter": wide},
        format="json",
    )
    assert resp.status_code == 400, resp.content
    assert any("conditions" in m or "maximum" in m for m in resp.json()["schema_errors"])


# ---------------------------------------------------------------------------
# RBAC gating
# ---------------------------------------------------------------------------
def test_read_only_member_can_list_but_not_create(api, org_ctx):
    org, owner, env = org_ctx
    reader = _add_member(org, "seg_reader@example.com")
    _grant_workspace(org, reader, env, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    assert api.get(f"/api/v1/{SLUG}/environments/{env.id}/segments").status_code == 200
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Nope", "environment_id": str(env.id), "filter": {}},
        format="json",
    )
    assert resp.status_code == 403


def test_write_member_can_create_segment(api, org_ctx):
    org, owner, env = org_ctx
    writer = _add_member(org, "seg_writer@example.com")
    _grant_workspace(org, writer, env, WorkspaceAccess.WRITE)
    api.force_authenticate(user=writer)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Mine", "environment_id": str(env.id), "filter": {}},
        format="json",
    )
    assert resp.status_code == 201


def test_read_only_member_cannot_edit_segment_403(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Locked", "environment_id": str(env.id), "filter": {}},
        format="json",
    ).json()
    reader = _add_member(org, "seg_ro_edit@example.com")
    _grant_workspace(org, reader, env, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    # Can see it (via list) but PUT → 403 (not 404).
    resp = api.put(
        f"/api/v1/{SLUG}/segments/{created['id']}",
        {"title": "Hacked", "filter": {}},
        format="json",
    )
    assert resp.status_code == 403


def test_member_without_access_404_on_segment_list(api, org_ctx):
    org, owner, env = org_ctx
    nobody = _add_member(org, "seg_nobody@example.com")
    api.force_authenticate(user=nobody)
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/segments")
    assert resp.status_code == 404


def test_non_member_forbidden(api, org_ctx):
    org, owner, env = org_ctx
    stranger = _mk_user("seg_stranger@example.com")
    api.force_authenticate(user=stranger)
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/segments")
    assert resp.status_code == 403


def test_evaluate_segment_read_only_member_allowed_and_pii_masked(api, org_ctx):
    """A read-only member CAN evaluate a segment (view_segment = read), AND the matched
    contacts' PII attribute values are MASKED on the evaluation read path (guarding the
    segment-eval endpoint against a PII leak, identical rule to the contact list/detail)."""
    org, owner, env = org_ctx
    _seed_attribute_keys(org, env)
    # Add a PII email key and a contact carrying it.
    connection.set_tenant(org)
    ContactAttributeKey.objects.create(
        environment=env, key="email", data_type=AttrType.STRING, is_pii=True
    )
    _mk_contact(env, plan="pro", email="leak@example.com")
    api.force_authenticate(user=owner)
    seg = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/segments",
        {"title": "Readable", "environment_id": str(env.id), "filter": {"attributeKey": "plan", "operator": "equals", "value": "pro"}},
        format="json",
    ).json()
    reader = _add_member(org, "seg_eval_reader@example.com")
    _grant_workspace(org, reader, env, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.get(f"/api/v1/{SLUG}/segments/{seg['id']}/contacts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    # The PII email value is masked for the read-only caller.
    email_attr = {a["key"]: a for a in body["results"][0]["attributes"]}["email"]
    assert email_attr["value"] == "***"
    assert email_attr["is_pii"] is True
