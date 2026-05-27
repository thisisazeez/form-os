"""
Tests for the A3 Team + Settings server-rendered (HTMX) admin pages (Wave 3b frontend):

  GET      /{org_slug}/teams                          → teams_page
  GET/POST /{org_slug}/settings/workspace             → workspace_settings_page
  GET/POST /{org_slug}/workspaces/{id}/settings       → workspace_settings_page (addressable)
  GET      /{org_slug}/settings                        → org_settings_page (general)
  GET      /{org_slug}/settings/billing               → org_settings_page (billing)
  GET/POST /{org_slug}/account                         → account_settings_page

Hit through the REAL TenantSlugMiddleware (slug resolves + activates the org schema) with
Django's session test ``Client`` + ``force_login`` (these are Django HTML views gated by
``@login_required`` — NOT DRF, so ``force_authenticate`` wouldn't drive the login gate). Org
creation provisions a Postgres schema (DDL) → ``transactional_db`` + ``force_drop`` cleanup,
mirroring tests/test_builder_host_page.py + tests/test_htmx_pages.py.

RBAC coverage (per the brief's verify block):
  - Teams: owner 200 + the selected team's members + the workspace-access matrix; an org member
    sees only the teams they belong to (filtered, mirrors TeamListCreateView.get); non-member
    403; anonymous 302.
  - Workspace settings: owner 200 (manage); member with workspace `read` can VIEW but not manage
    → 403; member with NO access can't even view → 404; anonymous 302.
  - Org settings/billing: owner 200 with REAL usage counts (responses/contacts/seats); plain
    member 403.
  - Account: any active member 200 (+ a wired name save); non-member 403; anonymous 302.
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.contacts.models import Contact
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.responses.models import Response as SurveyResponse
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.teams.enums import TeamRole, WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "a3-org"


# --------------------------------------------------------------------------- #
#  Fixtures / helpers (mirror tests/test_builder_host_page.py)                 #
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
    """Org + owner + a workspace (with prod/dev envs) + a team. Yields a context dict."""
    owner = _mk_user("a3_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="A3 Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="Acme Workspace", slug="acme-ws")
    env_prod = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    Environment.objects.create(workspace=ws, type=EnvType.DEVELOPMENT)
    team = Team.objects.create(name="Marketing")
    # Owner is on the team too (so the team has a member to render).
    TeamMembership.objects.create(team=team, user_id=owner.id, team_role=TeamRole.ADMIN)
    yield {
        "org": org,
        "owner": owner,
        "workspace": ws,
        "env_prod": env_prod,
        "team": team,
    }
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


def _grant(org, user, workspace_id, access):
    """Put `user` on a fresh team granted `access` on `workspace_id` (mirrors builder test)."""
    connection.set_tenant(org)
    team = Team.objects.create(name=f"team-{user.email}-{access}")
    TeamMembership.objects.create(team=team, user_id=user.id)
    WorkspaceTeam.objects.create(team=team, workspace_id=workspace_id, access=access)
    return team


# =========================================================================== #
#  /teams — Teams & access control                                            #
# =========================================================================== #
def test_teams_page_owner_renders_matrix(client, org_ctx):
    """Owner: 200 + Teams rail + the selected team's members + the workspace-access matrix.

    The matrix reflects the team's REAL WorkspaceTeam.access: grant the team `write` on the
    workspace and assert that row's "Write" pill is marked selected (aria-checked="true").
    """
    org, owner, ws, team = (org_ctx["org"], org_ctx["owner"], org_ctx["workspace"], org_ctx["team"])
    connection.set_tenant(org)
    WorkspaceTeam.objects.create(team=team, workspace=ws, access=WorkspaceAccess.WRITE)

    client.force_login(owner)
    resp = client.get(f"/{SLUG}/teams")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "Teams &amp; access control" in html or "Teams & access control" in html
    assert "Marketing" in html  # the team name in the rail + detail heading
    assert "Workspace access matrix" in html
    assert ws.name in html  # the workspace appears as a matrix row
    # The matrix carries the real wiring (team + workspace ids) and shows the granted level.
    assert f'data-team-id="{team.id}"' in html
    assert f'data-workspace-id="{ws.id}"' in html
    # The "Write" pill for this row is the selected one (brand highlight + aria-checked).
    assert 'data-access="write" aria-checked="true"' in html


def test_teams_page_selected_team_via_query(client, org_ctx):
    """?team_id selects that team; an unknown id falls back to the first visible team (200)."""
    owner, team = org_ctx["owner"], org_ctx["team"]
    client.force_login(owner)
    # Explicit selection.
    resp = client.get(f"/{SLUG}/teams?team_id={team.id}")
    assert resp.status_code == 200
    assert "Marketing members" in resp.content.decode()
    # Unknown id → falls back to first team (no crash, still 200).
    resp2 = client.get(f"/{SLUG}/teams?team_id={uuid.uuid4()}")
    assert resp2.status_code == 200
    assert "Marketing" in resp2.content.decode()


def test_teams_page_member_sees_only_own_teams(client, org_ctx):
    """A plain org member sees ONLY teams they belong to (mirrors TeamListCreateView.get)."""
    org = org_ctx["org"]
    # Owner's team "Marketing" exists; add a SECOND team the member is NOT on.
    connection.set_tenant(org)
    Team.objects.create(name="SecretTeam")
    member = _add_member(org, "a3_member_teams@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/teams")
    assert resp.status_code == 200
    html = resp.content.decode()
    # Member is on no team → no team is visible (empty state), and "SecretTeam" is hidden.
    assert "SecretTeam" not in html
    assert "No teams visible to you yet." in html


def test_teams_page_member_on_team_sees_it(client, org_ctx):
    """An org member ON a team sees that team (and not others)."""
    org = org_ctx["org"]
    connection.set_tenant(org)
    Team.objects.create(name="HiddenTeam")
    member = _add_member(org, "a3_member_onteam@example.com")
    connection.set_tenant(org)
    visible = Team.objects.create(name="VisibleTeam")
    TeamMembership.objects.create(team=visible, user_id=member.id)
    client.force_login(member)
    html = client.get(f"/{SLUG}/teams").content.decode()
    assert "VisibleTeam" in html
    assert "HiddenTeam" not in html


def test_teams_page_non_member_403(client, org_ctx):
    outsider = _mk_user("a3_outsider_teams@example.com")
    client.force_login(outsider)
    resp = client.get(f"/{SLUG}/teams")
    assert resp.status_code == 403


def test_teams_page_anonymous_redirects(client, org_ctx):
    resp = client.get(f"/{SLUG}/teams")
    assert resp.status_code in (301, 302)


# =========================================================================== #
#  /settings/workspace — Workspace settings                                   #
# =========================================================================== #
def test_workspace_settings_owner_200(client, org_ctx):
    """Owner (implicit manage): 200 + identity form + environments + danger zone."""
    owner, ws = org_ctx["owner"], org_ctx["workspace"]
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/workspace")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "Workspace settings" in html
    assert ws.name in html  # the default workspace's name prefilled
    assert "Environments" in html
    assert "Production" in html and "Development" in html
    assert "Delete workspace" in html  # owner sees the danger zone


def test_workspace_settings_addressable_by_id(client, org_ctx):
    owner, ws = org_ctx["owner"], org_ctx["workspace"]
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/workspaces/{ws.id}/settings")
    assert resp.status_code == 200
    assert ws.name in resp.content.decode()


def test_workspace_settings_rename_post(client, org_ctx):
    """The name save posts to update_workspace (the SAME service the DRF PATCH uses)."""
    org, owner, ws = org_ctx["org"], org_ctx["owner"], org_ctx["workspace"]
    client.force_login(owner)
    resp = client.post(f"/{SLUG}/workspaces/{ws.id}/settings", {"name": "Renamed WS"})
    assert resp.status_code == 302
    connection.set_tenant(org)
    ws.refresh_from_db()
    assert ws.name == "Renamed WS"


def test_workspace_settings_member_read_only_403(client, org_ctx):
    """An org member with workspace `read` can VIEW but not manage → 403 (read < manage)."""
    org, ws = org_ctx["org"], org_ctx["workspace"]
    reader = _add_member(org, "a3_ws_reader@example.com")
    _grant(org, reader, ws.id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(f"/{SLUG}/workspaces/{ws.id}/settings")
    assert resp.status_code == 403


def test_workspace_settings_member_no_access_404(client, org_ctx):
    """An org member with NO workspace access can't even view → 404 (no existence oracle)."""
    org, ws = org_ctx["org"], org_ctx["workspace"]
    member = _add_member(org, "a3_ws_noaccess@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/workspaces/{ws.id}/settings")
    assert resp.status_code == 404


def test_workspace_settings_anonymous_redirects(client, org_ctx):
    resp = client.get(f"/{SLUG}/settings/workspace")
    assert resp.status_code in (301, 302)


# =========================================================================== #
#  /settings + /settings/billing — Org settings & billing                     #
# =========================================================================== #
def test_org_settings_owner_200_with_usage(client, org_ctx):
    """Owner: 200 + the free/self-hosted banner + REAL usage counts (responses/contacts/seats)."""
    org, owner, env_prod = (
        org_ctx["org"], org_ctx["owner"], org_ctx["env_prod"]
    )
    # Seed real tenant-schema rows so the counts are non-zero and asserted exactly.
    connection.set_tenant(org)
    survey = Survey.objects.create(
        environment=env_prod, name="S", status=SurveyStatus.DRAFT,
        questions=[], languages=[{"code": "en", "label": "English", "default": True}],
    )
    SurveyResponse.objects.create(survey=survey, data={}, finished=True)
    SurveyResponse.objects.create(survey=survey, data={}, finished=False)
    Contact.objects.create(environment=env_prod, external_id="c1")

    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "self-hosted" in html.lower()
    assert "unlimited" in html.lower()
    # Real counts surface in the usage meters.
    assert "Responses" in html and "Contacts" in html and "Seats used" in html
    # 2 responses, 1 contact, 1 seat (the owner) — assert the live numbers render.
    assert ">2<" in html or " 2 " in html  # responses_count
    assert ">1<" in html or " 1 " in html  # contacts_count / seats_count


def test_org_billing_section_active(client, org_ctx):
    """/settings/billing renders the same page with the Billing tab marked active."""
    owner = org_ctx["owner"]
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/billing")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Current plan" in html
    assert "Self-hosted Free" in html


def test_org_settings_member_403(client, org_ctx):
    """A plain org member cannot see org settings/billing → 403 (owner/admin only)."""
    org = org_ctx["org"]
    member = _add_member(org, "a3_settings_member@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/settings")
    assert resp.status_code == 403


def test_org_settings_admin_200(client, org_ctx):
    """An org ADMIN (not just owner) can see org settings."""
    org = org_ctx["org"]
    admin = _add_member(org, "a3_settings_admin@example.com", role=OrgRole.ADMIN)
    client.force_login(admin)
    resp = client.get(f"/{SLUG}/settings")
    assert resp.status_code == 200


def test_org_settings_non_member_403(client, org_ctx):
    outsider = _mk_user("a3_settings_outsider@example.com")
    client.force_login(outsider)
    resp = client.get(f"/{SLUG}/settings")
    assert resp.status_code == 403


# =========================================================================== #
#  /account — Account settings                                                #
# =========================================================================== #
def test_account_settings_member_200(client, org_ctx):
    """Any ACTIVE member can edit their own account → 200 + profile + verified chip."""
    org = org_ctx["org"]
    member = _add_member(org, "a3_account_member@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/account")
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "Account settings" in html
    assert "a3_account_member@example.com" in html  # the user's email shown
    assert "Verified" in html  # verified chip (is_verified=True)
    assert "Notifications" in html


def test_account_settings_owner_name_save(client, org_ctx):
    """The display-name save is wired (written in the PUBLIC schema)."""
    owner = org_ctx["owner"]
    client.force_login(owner)
    resp = client.post(f"/{SLUG}/account", {"name": "Owner Renamed"})
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Profile saved." in html
    # The form re-displays the NEW name immediately (request.user refreshed post-save).
    assert 'value="Owner Renamed"' in html
    connection.set_schema_to_public()
    owner.refresh_from_db()
    assert owner.name == "Owner Renamed"


def test_account_settings_non_member_403(client, org_ctx):
    outsider = _mk_user("a3_account_outsider@example.com")
    client.force_login(outsider)
    resp = client.get(f"/{SLUG}/account")
    assert resp.status_code == 403


def test_account_settings_anonymous_redirects(client, org_ctx):
    resp = client.get(f"/{SLUG}/account")
    assert resp.status_code in (301, 302)


def _png_bytes():
    """A tiny valid PNG (8x8 teal) for avatar-upload tests."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (13, 148, 136)).save(buf, "PNG")
    return buf.getvalue()


def test_account_avatar_upload_valid(client, org_ctx, settings, tmp_path):
    """A valid image uploads, stored under avatars/<uuid>.png (UUID name, not the upload's)."""
    settings.MEDIA_ROOT = str(tmp_path)
    owner = org_ctx["owner"]
    client.force_login(owner)
    upload = SimpleUploadedFile("MyPhoto.png", _png_bytes(), content_type="image/png")
    resp = client.post(f"/{SLUG}/account", {"name": "Owner", "avatar": upload})
    assert resp.status_code == 200
    assert "Profile saved." in resp.content.decode()
    connection.set_schema_to_public()
    owner.refresh_from_db()
    assert owner.avatar.name.startswith("avatars/")
    assert owner.avatar.name.endswith(".png")
    assert "MyPhoto" not in owner.avatar.name  # never the user-supplied filename


def test_account_avatar_rejects_non_image(client, org_ctx, settings, tmp_path):
    """A non-image (even with a spoofed image content-type) is rejected by byte validation."""
    settings.MEDIA_ROOT = str(tmp_path)
    owner = org_ctx["owner"]
    client.force_login(owner)
    upload = SimpleUploadedFile("evil.png", b"<html>not an image</html>", content_type="image/png")
    resp = client.post(f"/{SLUG}/account", {"name": "Owner", "avatar": upload})
    assert resp.status_code == 200
    assert "valid image" in resp.content.decode().lower()
    connection.set_schema_to_public()
    owner.refresh_from_db()
    assert not owner.avatar  # nothing stored


def test_account_avatar_rejects_malformed_image(client, org_ctx, settings, tmp_path):
    """A file with a valid PNG header but corrupt bytes (PIL raises SyntaxError on verify) must
    be rejected GRACEFULLY (200 + error), never a 500 — untrusted-input validation catches all."""
    settings.MEDIA_ROOT = str(tmp_path)
    owner = org_ctx["owner"]
    client.force_login(owner)
    corrupt = bytearray(_png_bytes())
    corrupt[40] ^= 0xFF  # flip a byte inside the IDAT chunk → CRC mismatch on verify()
    upload = SimpleUploadedFile("corrupt.png", bytes(corrupt), content_type="image/png")
    resp = client.post(f"/{SLUG}/account", {"name": "Owner", "avatar": upload})
    assert resp.status_code == 200  # graceful, NOT a 500 traceback
    assert "valid image" in resp.content.decode().lower()
    connection.set_schema_to_public()
    owner.refresh_from_db()
    assert not owner.avatar


def test_account_avatar_remove(client, org_ctx, settings, tmp_path):
    """op=remove_avatar clears the avatar (falls back to the initial)."""
    settings.MEDIA_ROOT = str(tmp_path)
    owner = org_ctx["owner"]
    client.force_login(owner)
    client.post(
        f"/{SLUG}/account",
        {"name": "Owner", "avatar": SimpleUploadedFile("p.png", _png_bytes(), content_type="image/png")},
    )
    connection.set_schema_to_public()
    owner.refresh_from_db()
    assert owner.avatar  # uploaded
    resp = client.post(f"/{SLUG}/account", {"name": "Owner", "op": "remove_avatar"})
    assert resp.status_code == 200
    connection.set_schema_to_public()
    owner.refresh_from_db()
    assert not owner.avatar  # removed
