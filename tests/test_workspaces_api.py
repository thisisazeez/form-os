"""
HTTP-level tests for the Workspaces + Environments API (contracts/openapi.yaml — Workspaces).

Exercises the real request path including TenantSlugMiddleware: endpoints are hit at
``/api/v1/<slug>/workspaces...`` so the middleware resolves + activates the org schema and
``IsOrgMember`` + ``can()`` run against the routed org.

Org creation provisions a Postgres schema (DDL), so tests use ``transactional_db`` and clean
up with ``force_drop=True`` (same constraint as tests/conftest.py's demo_tenant). Auth uses
DRF ``force_authenticate``.

Covers: create (auto prod+dev environments), list filtering by effective access (rbac.md
§8.1), detail read/patch/delete gating, environments listing, and the ``effective_access``
convenience field (openapi Workspace).
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Workspace

User = get_user_model()

SLUG = "ws-api-org"


@pytest.fixture
def api():
    return APIClient()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    """Org + owner; yields (org, owner). Cleans up the schema after."""
    owner = _mk_user("ws_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="WS API Org", slug=SLUG, owner=owner)
    yield org, owner
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


def _grant_team_access(org, user, workspace_id, access):
    """Put ``user`` in a fresh team and link it to the workspace at ``access`` (tenant schema)."""
    connection.set_tenant(org)
    team = Team.objects.create(name=f"team-{user.email}-{access}")
    TeamMembership.objects.create(team=team, user_id=user.id)
    WorkspaceTeam.objects.create(team=team, workspace_id=workspace_id, access=access)


# ---------------------------------------------------------------------------
# create (POST /workspaces) — auto-creates prod + dev environments
# ---------------------------------------------------------------------------
def test_create_workspace_as_owner_creates_environments(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Marketing"}, format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["name"] == "Marketing"
    assert body["slug"] == "marketing"
    assert body["effective_access"] == "manage"  # owner → implicit manage
    # The read serializer also exposes the General config (recontact cooldown) + the enterprise
    # white-label flag, both at their creation defaults (no cooldown / branding shown).
    assert set(body) == {
        "id",
        "name",
        "slug",
        "recontact_days",
        "remove_branding",
        "effective_access",
        "created_at",
    }
    assert body["recontact_days"] is None
    assert body["remove_branding"] is False

    # Two environments auto-created (production + development), models.md §2.1/§2.2.
    connection.set_tenant(org)
    ws = Workspace.objects.get(pk=body["id"])
    types = set(ws.environments.values_list("type", flat=True))
    assert types == {EnvType.PRODUCTION, EnvType.DEVELOPMENT}


def test_create_workspace_member_forbidden(api, org_ctx):
    org, owner = org_ctx
    member = _add_member(org, "ws_create_member@example.com", OrgRole.MEMBER)
    api.force_authenticate(user=member)
    resp = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Nope"}, format="json")
    assert resp.status_code == 403


def test_create_workspace_admin_allowed(api, org_ctx):
    org, owner = org_ctx
    admin = _add_member(org, "ws_create_admin@example.com", OrgRole.ADMIN)
    api.force_authenticate(user=admin)
    resp = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "AdminWS"}, format="json")
    assert resp.status_code == 201


def test_create_workspace_explicit_slug_collision_409(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    first = api.post(
        f"/api/v1/{SLUG}/workspaces", {"name": "A", "slug": "shared"}, format="json"
    )
    assert first.status_code == 201
    dup = api.post(
        f"/api/v1/{SLUG}/workspaces", {"name": "B", "slug": "shared"}, format="json"
    )
    assert dup.status_code == 409


def test_create_workspace_non_member_403(api, org_ctx):
    org, owner = org_ctx
    outsider = _mk_user("ws_outsider@example.com")
    api.force_authenticate(user=outsider)
    resp = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "X"}, format="json")
    assert resp.status_code == 403  # IsOrgMember blocks before can()


# ---------------------------------------------------------------------------
# list (GET /workspaces) — filtered by effective access (rbac.md §8.1)
# ---------------------------------------------------------------------------
def test_list_workspaces_owner_sees_all(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    for name in ("One", "Two", "Three"):
        api.post(f"/api/v1/{SLUG}/workspaces", {"name": name}, format="json")
    resp = api.get(f"/api/v1/{SLUG}/workspaces")
    assert resp.status_code == 200
    body = resp.json()
    # The 3 created here + the "Default workspace" auto-created with the org (create_organization).
    assert len(body) == 4
    names = {w["name"] for w in body}
    assert {"One", "Two", "Three"}.issubset(names)
    assert all(w["effective_access"] == "manage" for w in body)  # owner → implicit manage


def test_list_workspaces_member_sees_only_granted(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    w_seen = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Seen"}, format="json").json()
    api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Hidden"}, format="json")

    member = _add_member(org, "ws_list_member@example.com", OrgRole.MEMBER)
    _grant_team_access(org, member, w_seen["id"], WorkspaceAccess.READ)

    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/workspaces")
    assert resp.status_code == 200
    body = resp.json()
    # Only the granted workspace is visible (default-deny on the other).
    assert len(body) == 1
    assert body[0]["id"] == w_seen["id"]
    assert body[0]["effective_access"] == "read"


def test_list_workspaces_member_no_teams_empty(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Solo"}, format="json")
    member = _add_member(org, "ws_list_noteam@example.com", OrgRole.MEMBER)
    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/workspaces")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# detail GET / PATCH / DELETE
# ---------------------------------------------------------------------------
def test_get_workspace_reader(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Readable"}, format="json").json()
    reader = _add_member(org, "ws_get_reader@example.com", OrgRole.MEMBER)
    _grant_team_access(org, reader, ws["id"], WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.get(f"/api/v1/{SLUG}/workspaces/{ws['id']}")
    assert resp.status_code == 200
    assert resp.json()["effective_access"] == "read"


def test_get_workspace_no_access_404(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Hidden"}, format="json").json()
    member = _add_member(org, "ws_get_noacc@example.com", OrgRole.MEMBER)
    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/workspaces/{ws['id']}")
    # Member with no access → 404 (no existence oracle; rbac.md NotFound semantics).
    assert resp.status_code == 404


def test_patch_workspace_requires_manage(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Orig"}, format="json").json()

    # write-level member cannot manage (rename).
    writer = _add_member(org, "ws_patch_writer@example.com", OrgRole.MEMBER)
    _grant_team_access(org, writer, ws["id"], WorkspaceAccess.WRITE)
    api.force_authenticate(user=writer)
    resp = api.patch(
        f"/api/v1/{SLUG}/workspaces/{ws['id']}", {"name": "Renamed"}, format="json"
    )
    assert resp.status_code == 403

    # manage-level member can rename.
    manager = _add_member(org, "ws_patch_manager@example.com", OrgRole.MEMBER)
    _grant_team_access(org, manager, ws["id"], WorkspaceAccess.MANAGE)
    api.force_authenticate(user=manager)
    resp = api.patch(
        f"/api/v1/{SLUG}/workspaces/{ws['id']}", {"name": "Renamed"}, format="json"
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"
    # slug immutable.
    assert resp.json()["slug"] == ws["slug"]


def test_delete_workspace_owner_admin_only(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "ToDelete"}, format="json").json()

    # A manage-level member is NOT org owner/admin → cannot delete (delete_workspace is org-level).
    manager = _add_member(org, "ws_del_manager@example.com", OrgRole.MEMBER)
    _grant_team_access(org, manager, ws["id"], WorkspaceAccess.MANAGE)
    api.force_authenticate(user=manager)
    resp = api.delete(f"/api/v1/{SLUG}/workspaces/{ws['id']}")
    assert resp.status_code == 403

    # Owner can delete.
    api.force_authenticate(user=owner)
    resp = api.delete(f"/api/v1/{SLUG}/workspaces/{ws['id']}")
    assert resp.status_code == 204
    connection.set_tenant(org)
    assert not Workspace.objects.filter(pk=ws["id"]).exists()


# ---------------------------------------------------------------------------
# environments (GET /workspaces/{id}/environments)
# ---------------------------------------------------------------------------
def test_list_environments(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Envs"}, format="json").json()
    resp = api.get(f"/api/v1/{SLUG}/workspaces/{ws['id']}/environments")
    assert resp.status_code == 200
    body = resp.json()
    assert {e["type"] for e in body} == {EnvType.PRODUCTION, EnvType.DEVELOPMENT}
    assert all(e["workspace_id"] == ws["id"] for e in body)
    assert all(set(e) == {"id", "workspace_id", "type", "created_at"} for e in body)


def test_list_environments_no_access_404(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Priv"}, format="json").json()
    member = _add_member(org, "ws_env_noacc@example.com", OrgRole.MEMBER)
    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/workspaces/{ws['id']}/environments")
    assert resp.status_code == 404


def test_workspace_endpoints_require_auth(api, org_ctx):
    org, owner = org_ctx
    resp = api.get(f"/api/v1/{SLUG}/workspaces")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# N+1 guard (rbac.md §7 "O(1) queries" / §8.1 list filtering) — the access map must NOT
# grow queries with workspace count. Tested at the helper level (the HTTP path adds variable
# session/auth queries; the helper is the contract surface reviewers check).
# ---------------------------------------------------------------------------
def test_list_access_map_is_query_bounded(api, org_ctx, django_assert_max_num_queries):
    from apps.workspaces.access import readable_workspaces_with_access

    org, owner = org_ctx
    api.force_authenticate(user=owner)
    # Owner sees all; create several workspaces, then a member granted access to all of them.
    ws_ids = []
    for n in range(5):
        ws_ids.append(
            api.post(f"/api/v1/{SLUG}/workspaces", {"name": f"W{n}"}, format="json").json()["id"]
        )
    member = _add_member(org, "n1_member@example.com", OrgRole.MEMBER)
    connection.set_tenant(org)
    team = Team.objects.create(name="n1-team")
    TeamMembership.objects.create(team=team, user_id=member.id)
    for ws_id in ws_ids:
        WorkspaceTeam.objects.create(team=team, workspace_id=ws_id, access=WorkspaceAccess.READ)

    # Member path: membership(public) + team-ids + WorkspaceTeam + workspaces fetch — a small
    # CONSTANT number of queries regardless of the 5 workspaces (NO per-workspace can()).
    connection.set_tenant(org)
    with django_assert_max_num_queries(6):
        workspaces, access_map = readable_workspaces_with_access(member)
    assert len(workspaces) == 5
    assert set(access_map.values()) == {"read"}
