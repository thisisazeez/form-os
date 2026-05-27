"""
Tests for the INTERACTIVE Teams admin (HTMX) surface (apps.teams.views_htmx) — the writes the
read-only page contract (tests/test_team_settings_admin.py) deliberately leaves untouched:

  POST /{org_slug}/teams                                  → create team (modal → redirect)
  POST /{org_slug}/teams/<id>/access                      → grant / revoke workspace access (row partial)
  POST /{org_slug}/teams/<id>/members                     → add an org member (members partial)
  POST /{org_slug}/teams/<id>/members/<uid>/remove        → remove a member (members partial)

Hit through the REAL TenantSlugMiddleware (slug resolves + activates the org schema) with
Django's session ``Client`` + ``force_login`` (these are @login_required HTML views, NOT DRF).
Org creation provisions a Postgres schema (DDL) → ``transactional_db`` + ``force_drop`` cleanup,
mirroring tests/test_team_settings_admin.py.

RBAC + behavior coverage (the brief's verify block):
  - Matrix grant/revoke: org owner/admin allowed (grant_workspace_access); a plain member denied
    (403). Grant UPSERTs; "none" revokes (deletes the row). The LAST delegated manage path is
    guarded — lowering/revoking the only managing team is refused (409-equivalent inline error,
    state unchanged).
  - Create team: owner/admin allowed; member denied (403); duplicate name → friendly error.
  - Add/remove member: org owner/admin OR team-admin of THIS team allowed; a plain member (not a
    team-admin) denied (403). Add validates active-org-membership; remove is idempotent.
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.teams.enums import TeamRole, WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "ti-org"


# --------------------------------------------------------------------------- #
#  Fixtures / helpers (mirror tests/test_team_settings_admin.py)               #
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return Client()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True, name="")


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    """Org + owner + a workspace (prod/dev envs) + a team (owner is its admin)."""
    owner = _mk_user("ti_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="TI Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="Acme Workspace", slug="acme-ws")
    env_prod = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    Environment.objects.create(workspace=ws, type=EnvType.DEVELOPMENT)
    team = Team.objects.create(name="Marketing")
    TeamMembership.objects.create(team=team, user_id=owner.id, team_role=TeamRole.ADMIN)
    yield {"org": org, "owner": owner, "workspace": ws, "env_prod": env_prod, "team": team}
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


def _access(org, team, ws):
    """Read a team's current WorkspaceTeam.access for a workspace (None if no row)."""
    connection.set_tenant(org)
    return (
        WorkspaceTeam.objects.filter(team=team, workspace=ws)
        .values_list("access", flat=True)
        .first()
    )


# =========================================================================== #
#  Workspace-access matrix — grant / revoke                                   #
# =========================================================================== #
def test_matrix_grant_as_owner(client, org_ctx):
    """Owner grants the team `write` → row partial reflects it; the WorkspaceTeam row exists."""
    org, owner, ws, team = org_ctx["org"], org_ctx["owner"], org_ctx["workspace"], org_ctx["team"]
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/teams/{team.id}/access",
        {"workspace_id": str(ws.id), "access": "write"},
    )
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    # The returned row partial marks Write as the selected pill.
    assert 'data-access="write" aria-checked="true"' in html
    assert _access(org, team, ws) == WorkspaceAccess.WRITE


def test_matrix_update_existing_grant(client, org_ctx):
    """Granting again UPSERTS (read → manage), not a duplicate row."""
    org, owner, ws, team = org_ctx["org"], org_ctx["owner"], org_ctx["workspace"], org_ctx["team"]
    connection.set_tenant(org)
    WorkspaceTeam.objects.create(team=team, workspace=ws, access=WorkspaceAccess.READ)
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/teams/{team.id}/access",
        {"workspace_id": str(ws.id), "access": "manage"},
    )
    assert resp.status_code == 200
    assert _access(org, team, ws) == WorkspaceAccess.MANAGE
    connection.set_tenant(org)
    assert WorkspaceTeam.objects.filter(team=team, workspace=ws).count() == 1


def test_matrix_revoke_none_deletes_row(client, org_ctx):
    """Selecting "none" REVOKES — the WorkspaceTeam row is deleted (when not the last manage)."""
    org, owner, ws, team = org_ctx["org"], org_ctx["owner"], org_ctx["workspace"], org_ctx["team"]
    connection.set_tenant(org)
    WorkspaceTeam.objects.create(team=team, workspace=ws, access=WorkspaceAccess.WRITE)
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/teams/{team.id}/access",
        {"workspace_id": str(ws.id), "access": "none"},
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    # "None" is now the selected pill, and the row is gone.
    assert _access(org, team, ws) is None
    assert ">None</button>" in html


def test_matrix_last_manage_path_guard_blocks_revoke(client, org_ctx):
    """Revoking the ONLY team that can manage a workspace is refused (inline error, unchanged)."""
    org, owner, ws, team = org_ctx["org"], org_ctx["owner"], org_ctx["workspace"], org_ctx["team"]
    connection.set_tenant(org)
    WorkspaceTeam.objects.create(team=team, workspace=ws, access=WorkspaceAccess.MANAGE)
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/teams/{team.id}/access",
        {"workspace_id": str(ws.id), "access": "none"},
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "only team that can" in html.lower()
    # State is UNCHANGED — still manage.
    assert _access(org, team, ws) == WorkspaceAccess.MANAGE


def test_matrix_last_manage_path_guard_blocks_lower(client, org_ctx):
    """Lowering the only managing team to `read` is refused (guarded); unchanged at manage."""
    org, owner, ws, team = org_ctx["org"], org_ctx["owner"], org_ctx["workspace"], org_ctx["team"]
    connection.set_tenant(org)
    WorkspaceTeam.objects.create(team=team, workspace=ws, access=WorkspaceAccess.MANAGE)
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/teams/{team.id}/access",
        {"workspace_id": str(ws.id), "access": "read"},
    )
    assert resp.status_code == 200
    assert _access(org, team, ws) == WorkspaceAccess.MANAGE


def test_matrix_lower_allowed_when_another_manage_exists(client, org_ctx):
    """Lowering IS allowed when ANOTHER team still manages the workspace (path preserved)."""
    org, owner, ws, team = org_ctx["org"], org_ctx["owner"], org_ctx["workspace"], org_ctx["team"]
    connection.set_tenant(org)
    WorkspaceTeam.objects.create(team=team, workspace=ws, access=WorkspaceAccess.MANAGE)
    other = Team.objects.create(name="Ops")
    WorkspaceTeam.objects.create(team=other, workspace=ws, access=WorkspaceAccess.MANAGE)
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/teams/{team.id}/access",
        {"workspace_id": str(ws.id), "access": "read"},
    )
    assert resp.status_code == 200
    assert _access(org, team, ws) == WorkspaceAccess.READ


def test_matrix_grant_denied_for_plain_member(client, org_ctx):
    """A plain org member cannot grant workspace access → 403 (grant is owner/admin only)."""
    org, ws, team = org_ctx["org"], org_ctx["workspace"], org_ctx["team"]
    member = _add_member(org, "ti_member_grant@example.com")
    client.force_login(member)
    resp = client.post(
        f"/{SLUG}/teams/{team.id}/access",
        {"workspace_id": str(ws.id), "access": "write"},
    )
    assert resp.status_code == 403
    # Nothing was written.
    assert _access(org, team, ws) is None


def test_matrix_grant_admin_allowed(client, org_ctx):
    """An org ADMIN (not just owner) can grant access."""
    org, ws, team = org_ctx["org"], org_ctx["workspace"], org_ctx["team"]
    admin = _add_member(org, "ti_admin_grant@example.com", role=OrgRole.ADMIN)
    client.force_login(admin)
    resp = client.post(
        f"/{SLUG}/teams/{team.id}/access",
        {"workspace_id": str(ws.id), "access": "read"},
    )
    assert resp.status_code == 200
    assert _access(org, team, ws) == WorkspaceAccess.READ


# =========================================================================== #
#  Create team                                                                #
# =========================================================================== #
def test_create_team_as_owner(client, org_ctx):
    """Owner POSTs a new team → redirect to ?team_id=<new>; the team exists."""
    org, owner = org_ctx["org"], org_ctx["owner"]
    client.force_login(owner)
    resp = client.post(f"/{SLUG}/teams", {"name": "Engineering"})
    assert resp.status_code == 302
    assert "team_id=" in resp["Location"]
    connection.set_tenant(org)
    assert Team.objects.filter(name="Engineering").exists()


def test_create_team_admin_allowed(client, org_ctx):
    org = org_ctx["org"]
    admin = _add_member(org, "ti_admin_create@example.com", role=OrgRole.ADMIN)
    client.force_login(admin)
    resp = client.post(f"/{SLUG}/teams", {"name": "Support"})
    assert resp.status_code == 302
    connection.set_tenant(org)
    assert Team.objects.filter(name="Support").exists()


def test_create_team_denied_for_plain_member(client, org_ctx):
    """A plain org member cannot create a team → 403 (create_team is owner/admin only)."""
    org = org_ctx["org"]
    member = _add_member(org, "ti_member_create@example.com")
    client.force_login(member)
    resp = client.post(f"/{SLUG}/teams", {"name": "Sneaky"})
    assert resp.status_code == 403
    connection.set_tenant(org)
    assert not Team.objects.filter(name="Sneaky").exists()


def test_create_team_duplicate_name_friendly_error(client, org_ctx):
    """A duplicate (case-insensitive) name → redirect back with a create_error (no crash)."""
    org, owner = org_ctx["org"], org_ctx["owner"]
    client.force_login(owner)
    # "Marketing" already exists in the fixture.
    resp = client.post(f"/{SLUG}/teams", {"name": "marketing"})
    assert resp.status_code == 302
    assert "create_error=" in resp["Location"]
    connection.set_tenant(org)
    # No second Marketing-named team was created.
    assert Team.objects.filter(name__iexact="marketing").count() == 1


# =========================================================================== #
#  Add / remove team member                                                   #
# =========================================================================== #
def test_add_member_as_owner(client, org_ctx):
    """Owner adds an existing org member → members partial includes them; row exists."""
    org, owner, team = org_ctx["org"], org_ctx["owner"], org_ctx["team"]
    newbie = _add_member(org, "ti_newbie@example.com")
    client.force_login(owner)
    resp = client.post(f"/{SLUG}/teams/{team.id}/members", {"user_id": str(newbie.id)})
    assert resp.status_code == 200, resp.content
    assert "ti_newbie@example.com" in resp.content.decode()
    connection.set_tenant(org)
    assert TeamMembership.objects.filter(team=team, user_id=newbie.id).exists()


def test_add_member_team_admin_allowed(client, org_ctx):
    """A team-admin (not org admin) of THIS team can add members (delegated membership)."""
    org = org_ctx["org"]
    connection.set_tenant(org)
    team = Team.objects.create(name="Delegated")
    lead = _add_member(org, "ti_team_admin@example.com")
    connection.set_tenant(org)
    TeamMembership.objects.create(team=team, user_id=lead.id, team_role=TeamRole.ADMIN)
    newbie = _add_member(org, "ti_delegated_newbie@example.com")
    client.force_login(lead)
    resp = client.post(f"/{SLUG}/teams/{team.id}/members", {"user_id": str(newbie.id)})
    assert resp.status_code == 200
    connection.set_tenant(org)
    assert TeamMembership.objects.filter(team=team, user_id=newbie.id).exists()


def test_add_member_denied_for_plain_member(client, org_ctx):
    """A plain org member (not a team-admin) cannot manage membership → 403."""
    org, team = org_ctx["org"], org_ctx["team"]
    member = _add_member(org, "ti_member_addtest@example.com")
    target = _add_member(org, "ti_target@example.com")
    client.force_login(member)
    resp = client.post(f"/{SLUG}/teams/{team.id}/members", {"user_id": str(target.id)})
    assert resp.status_code == 403
    connection.set_tenant(org)
    assert not TeamMembership.objects.filter(team=team, user_id=target.id).exists()


def test_add_member_non_org_member_rejected(client, org_ctx):
    """Adding a user who is NOT an active org member → inline error (cross-schema validation)."""
    org, owner, team = org_ctx["org"], org_ctx["owner"], org_ctx["team"]
    outsider = _mk_user("ti_outsider_add@example.com")  # no Membership
    client.force_login(owner)
    resp = client.post(f"/{SLUG}/teams/{team.id}/members", {"user_id": str(outsider.id)})
    assert resp.status_code == 200
    assert "not an active member" in resp.content.decode().lower()
    connection.set_tenant(org)
    assert not TeamMembership.objects.filter(team=team, user_id=outsider.id).exists()


def test_remove_member_as_owner(client, org_ctx):
    """Owner removes a member → members partial no longer lists them; row gone."""
    org, owner, team = org_ctx["org"], org_ctx["owner"], org_ctx["team"]
    victim = _add_member(org, "ti_victim@example.com")
    connection.set_tenant(org)
    TeamMembership.objects.create(team=team, user_id=victim.id)
    client.force_login(owner)
    resp = client.post(f"/{SLUG}/teams/{team.id}/members/{victim.id}/remove")
    assert resp.status_code == 200
    # The row is gone from the DB; the partial no longer shows a Remove control for the victim
    # (they may reappear in the Add-member <select> as a candidate, which is expected).
    assert f"/members/{victim.id}/remove" not in resp.content.decode()
    connection.set_tenant(org)
    assert not TeamMembership.objects.filter(team=team, user_id=victim.id).exists()


def test_remove_member_denied_for_plain_member(client, org_ctx):
    """A plain org member cannot remove a team member → 403."""
    org, owner, team = org_ctx["org"], org_ctx["owner"], org_ctx["team"]
    member = _add_member(org, "ti_member_removetest@example.com")
    client.force_login(member)
    resp = client.post(f"/{SLUG}/teams/{team.id}/members/{owner.id}/remove")
    assert resp.status_code == 403
    connection.set_tenant(org)
    # The owner is still on the team.
    assert TeamMembership.objects.filter(team=team, user_id=owner.id).exists()


def test_remove_member_idempotent(client, org_ctx):
    """Removing an absent member is a no-op → still 200 (idempotent)."""
    owner, team = org_ctx["owner"], org_ctx["team"]
    client.force_login(owner)
    resp = client.post(f"/{SLUG}/teams/{team.id}/members/{uuid.uuid4()}/remove")
    assert resp.status_code == 200
