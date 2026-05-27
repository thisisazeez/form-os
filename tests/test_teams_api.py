"""
HTTP-level tests for the Teams API — Team CRUD, TeamMembership, WorkspaceTeam
(contracts/openapi.yaml — Teams tag).

Exercises the real request path (TenantSlugMiddleware → IsOrgMember → can()). Org creation
provisions a Postgres schema, so tests use ``transactional_db`` + ``force_drop=True``. Auth
uses DRF ``force_authenticate``.

Covers: team create/list(filtered)/detail(hydrated members)/rename/delete; add/remove member
(cross-schema membership validation, team-admin delegation); grant/update workspace access
(UPSERT). Member listings assert the openapi UserBrief shape (from hydrate_users()).
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.teams.enums import TeamRole, WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam

User = get_user_model()

SLUG = "teams-api-org"


@pytest.fixture
def api():
    return APIClient()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", name=email.split("@")[0], is_verified=True)


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    owner = _mk_user("teams_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Teams API Org", slug=SLUG, owner=owner)
    yield org, owner
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


# ---------------------------------------------------------------------------
# create / list / detail / rename / delete
# ---------------------------------------------------------------------------
def test_create_team_as_owner(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(f"/api/v1/{SLUG}/teams", {"name": "Engineering"}, format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["name"] == "Engineering"
    assert body["member_count"] == 0
    assert set(body) == {"id", "name", "member_count", "created_at"}


def test_create_team_member_forbidden(api, org_ctx):
    org, owner = org_ctx
    member = _add_member(org, "tc_member@example.com")
    api.force_authenticate(user=member)
    resp = api.post(f"/api/v1/{SLUG}/teams", {"name": "Nope"}, format="json")
    assert resp.status_code == 403


def test_create_team_duplicate_name_409(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    assert api.post(f"/api/v1/{SLUG}/teams", {"name": "Dup"}, format="json").status_code == 201
    # Case-insensitive collision.
    resp = api.post(f"/api/v1/{SLUG}/teams", {"name": "dup"}, format="json")
    assert resp.status_code == 409


def test_list_teams_owner_sees_all(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    for n in ("A", "B"):
        api.post(f"/api/v1/{SLUG}/teams", {"name": n}, format="json")
    resp = api.get(f"/api/v1/{SLUG}/teams")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_teams_member_sees_only_their_teams(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    t_in = api.post(f"/api/v1/{SLUG}/teams", {"name": "InTeam"}, format="json").json()
    api.post(f"/api/v1/{SLUG}/teams", {"name": "NotMyTeam"}, format="json")

    member = _add_member(org, "tl_member@example.com")
    connection.set_tenant(org)
    TeamMembership.objects.create(team_id=t_in["id"], user_id=member.id)

    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/teams")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == t_in["id"]


def test_team_detail_hydrated_members(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "Squad"}, format="json").json()
    u1 = _add_member(org, "td_u1@example.com")
    connection.set_tenant(org)
    TeamMembership.objects.create(team_id=team["id"], user_id=u1.id, team_role=TeamRole.ADMIN)

    resp = api.get(f"/api/v1/{SLUG}/teams/{team['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["member_count"] == 1
    assert "members" in body and "workspace_access" in body  # TeamDetail
    member = body["members"][0]
    # UserBrief shape from hydrate_users().
    assert set(member["user"]) == {"id", "email", "name"}
    assert member["user"]["email"] == "td_u1@example.com"
    assert member["team_role"] == TeamRole.ADMIN


def test_rename_team(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "Old"}, format="json").json()
    resp = api.patch(f"/api/v1/{SLUG}/teams/{team['id']}", {"name": "New"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


def test_delete_team(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "Temp"}, format="json").json()
    resp = api.delete(f"/api/v1/{SLUG}/teams/{team['id']}")
    assert resp.status_code == 204
    connection.set_tenant(org)
    assert not Team.objects.filter(pk=team["id"]).exists()


def test_member_cannot_delete_team_403(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "Protected"}, format="json").json()
    member = _add_member(org, "tdel_member@example.com")
    api.force_authenticate(user=member)
    resp = api.delete(f"/api/v1/{SLUG}/teams/{team['id']}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# membership add / remove
# ---------------------------------------------------------------------------
def test_add_team_member_as_owner(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "AddHere"}, format="json").json()
    new_member = _add_member(org, "add_member@example.com")
    resp = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/members",
        {"user_id": str(new_member.id), "team_role": TeamRole.MEMBER},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["user"]["email"] == "add_member@example.com"
    assert body["team_role"] == TeamRole.MEMBER


def test_add_team_member_not_org_member_409(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "T"}, format="json").json()
    # A user_id that is NOT an org member (cross-schema validation, models.md §2.4).
    resp = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/members",
        {"user_id": str(uuid.uuid4())},
        format="json",
    )
    assert resp.status_code == 409


def test_add_team_member_duplicate_409(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "T2"}, format="json").json()
    m = _add_member(org, "dup_member@example.com")
    first = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/members",
        {"user_id": str(m.id)},
        format="json",
    )
    assert first.status_code == 201
    dup = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/members",
        {"user_id": str(m.id)},
        format="json",
    )
    assert dup.status_code == 409


def test_team_admin_can_add_member_to_own_team(api, org_ctx):
    """rbac.md §5: a team-admin (org member) can manage THIS team's membership."""
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "Delegated"}, format="json").json()
    lead = _add_member(org, "team_lead@example.com")  # org member
    connection.set_tenant(org)
    TeamMembership.objects.create(team_id=team["id"], user_id=lead.id, team_role=TeamRole.ADMIN)

    invitee = _add_member(org, "lead_invitee@example.com")
    api.force_authenticate(user=lead)
    resp = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/members",
        {"user_id": str(invitee.id)},
        format="json",
    )
    assert resp.status_code == 201


def test_plain_member_cannot_add_team_member_403(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "Closed"}, format="json").json()
    member = _add_member(org, "plain_member@example.com")  # not a team-admin
    invitee = _add_member(org, "would_be@example.com")
    api.force_authenticate(user=member)
    resp = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/members",
        {"user_id": str(invitee.id)},
        format="json",
    )
    assert resp.status_code == 403


def test_remove_team_member(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "RemoveHere"}, format="json").json()
    m = _add_member(org, "remove_me@example.com")
    connection.set_tenant(org)
    TeamMembership.objects.create(team_id=team["id"], user_id=m.id)

    resp = api.delete(f"/api/v1/{SLUG}/teams/{team['id']}/members/{m.id}")
    assert resp.status_code == 204
    connection.set_tenant(org)
    assert not TeamMembership.objects.filter(team_id=team["id"], user_id=m.id).exists()


# ---------------------------------------------------------------------------
# workspace-access grant (WorkspaceTeam UPSERT)
# ---------------------------------------------------------------------------
def test_grant_workspace_access_as_owner(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "GrantTeam"}, format="json").json()
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "GrantWS"}, format="json").json()

    resp = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/workspace-access",
        {"workspace_id": ws["id"], "access": WorkspaceAccess.WRITE},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["team_id"] == team["id"]
    assert body["workspace_id"] == ws["id"]
    assert body["access"] == WorkspaceAccess.WRITE
    assert set(body) == {"id", "team_id", "workspace_id", "access"}


def test_grant_workspace_access_upsert(api, org_ctx):
    """Re-granting updates the SAME row's access (one WorkspaceTeam per team+workspace)."""
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "UpsertTeam"}, format="json").json()
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "UpsertWS"}, format="json").json()

    api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/workspace-access",
        {"workspace_id": ws["id"], "access": WorkspaceAccess.READ},
        format="json",
    )
    resp = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/workspace-access",
        {"workspace_id": ws["id"], "access": WorkspaceAccess.MANAGE},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.json()["access"] == WorkspaceAccess.MANAGE
    connection.set_tenant(org)
    assert WorkspaceTeam.objects.filter(team_id=team["id"], workspace_id=ws["id"]).count() == 1


def test_grant_workspace_access_member_forbidden(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "X"}, format="json").json()
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "Y"}, format="json").json()
    member = _add_member(org, "grant_member@example.com")
    api.force_authenticate(user=member)
    resp = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/workspace-access",
        {"workspace_id": ws["id"], "access": WorkspaceAccess.READ},
        format="json",
    )
    assert resp.status_code == 403


def test_grant_workspace_access_bad_workspace_400(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "BadWS"}, format="json").json()
    resp = api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/workspace-access",
        {"workspace_id": str(uuid.uuid4()), "access": WorkspaceAccess.READ},
        format="json",
    )
    assert resp.status_code == 400


def test_grant_then_member_can_access_workspace(api, org_ctx):
    """End-to-end: grant a team write on a workspace → its member can now list/see it."""
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    team = api.post(f"/api/v1/{SLUG}/teams", {"name": "E2E"}, format="json").json()
    ws = api.post(f"/api/v1/{SLUG}/workspaces", {"name": "E2EWS"}, format="json").json()
    member = _add_member(org, "e2e_member@example.com")
    # Put member in the team, then grant the team access.
    api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/members",
        {"user_id": str(member.id)},
        format="json",
    )
    api.post(
        f"/api/v1/{SLUG}/teams/{team['id']}/workspace-access",
        {"workspace_id": ws["id"], "access": WorkspaceAccess.WRITE},
        format="json",
    )
    # The member now sees the workspace at write.
    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/workspaces")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == ws["id"]
    assert body[0]["effective_access"] == "write"
