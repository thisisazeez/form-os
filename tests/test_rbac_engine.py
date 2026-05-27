"""
RBAC engine tests — ``apps.common.permissions.effective_access`` + ``can`` + the
``{% can %}`` template tag (contracts/rbac.md §2–§8).

These are ENGINE-level (no HTTP): they activate a tenant schema with ``demo_tenant``, write
public ``Membership`` rows + tenant ``Team``/``TeamMembership``/``WorkspaceTeam`` rows, and
assert the frozen rules directly. The cross-schema dance (public Membership ⟂ tenant Teams,
matched by ``user_id`` value — ADR-002 / rbac.md §4) is exercised end-to-end here.

Covered:
  - effective_access: max-over-teams; owner/admin implicit manage; non-member → None;
    member with no team → None; ordering read<write<manage.
  - can() workspace matrix (each level's allowed actions; default-deny).
  - can() org matrix (view/edit/delete org, invite, apikeys, webhooks).
  - special predicates: view_team (in-team), manage_team_membership (team-admin),
    change_member_role / remove_member last-owner invariant + admin-vs-owner.
  - superuser does NOT bypass can(); anonymous → False; unknown action → ValueError.
  - the {% can %} assignment tag delegates to can().
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context

from apps.common.permissions import can, effective_access, workspace_of
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership
from apps.teams.enums import TeamRole, WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()


# ---------------------------------------------------------------------------
# helpers — create public Users/Memberships and tenant Teams/links
# ---------------------------------------------------------------------------
def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _mk_membership(org, user, role):
    with schema_context(get_public_schema_name()):
        return Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )


def _activate(org):
    connection.set_tenant(org)


def _mk_workspace(name="WS", slug=None):
    return Workspace.objects.create(name=name, slug=slug or f"ws-{uuid.uuid4().hex[:8]}")


# ===========================================================================
# effective_access (rbac.md §4)
# ===========================================================================
@pytest.mark.django_db
def test_effective_access_owner_admin_implicit_manage(demo_tenant):
    owner = _mk_user("ea_owner@example.com")
    admin = _mk_user("ea_admin@example.com")
    _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _mk_membership(demo_tenant, admin, OrgRole.ADMIN)
    _activate(demo_tenant)
    ws = _mk_workspace()
    # No WorkspaceTeam rows at all — implicit manage still applies (computed, not stored).
    assert effective_access(owner, ws) == "manage"
    assert effective_access(admin, ws) == "manage"


@pytest.mark.django_db
def test_effective_access_non_member_is_none(demo_tenant):
    outsider = _mk_user("ea_outsider@example.com")  # no Membership in this org
    _activate(demo_tenant)
    ws = _mk_workspace()
    assert effective_access(outsider, ws) is None


@pytest.mark.django_db
def test_effective_access_member_no_team_is_none(demo_tenant):
    member = _mk_user("ea_member_noteam@example.com")
    _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)
    ws = _mk_workspace()
    # Org member with zero teams linked → no access (default-deny posture, rbac.md §3).
    assert effective_access(member, ws) is None


@pytest.mark.django_db
def test_effective_access_max_over_teams(demo_tenant):
    """A member in TWO teams linked to the same workspace gets the MAX access (rbac.md §4)."""
    member = _mk_user("ea_maxteams@example.com")
    _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)
    ws = _mk_workspace()
    t_read = Team.objects.create(name="Readers")
    t_write = Team.objects.create(name="Writers")
    TeamMembership.objects.create(team=t_read, user_id=member.id)
    TeamMembership.objects.create(team=t_write, user_id=member.id)
    WorkspaceTeam.objects.create(team=t_read, workspace=ws, access=WorkspaceAccess.READ)
    WorkspaceTeam.objects.create(team=t_write, workspace=ws, access=WorkspaceAccess.WRITE)
    # max(read, write) = write.
    assert effective_access(member, ws) == "write"


@pytest.mark.django_db
def test_effective_access_member_single_team_each_level(demo_tenant):
    member = _mk_user("ea_levels@example.com")
    _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)
    team = Team.objects.create(name="OneTeam")
    TeamMembership.objects.create(team=team, user_id=member.id)
    for level, expected in [
        (WorkspaceAccess.READ, "read"),
        (WorkspaceAccess.WRITE, "write"),
        (WorkspaceAccess.MANAGE, "manage"),
    ]:
        ws = _mk_workspace(slug=f"ws-{level}")
        WorkspaceTeam.objects.create(team=team, workspace=ws, access=level)
        assert effective_access(member, ws) == expected


@pytest.mark.django_db
def test_effective_access_team_link_other_workspace_does_not_leak(demo_tenant):
    """Access to workspace A via a team must NOT grant access to workspace B."""
    member = _mk_user("ea_isolation@example.com")
    _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)
    ws_a = _mk_workspace(slug="ws-a")
    ws_b = _mk_workspace(slug="ws-b")
    team = Team.objects.create(name="OnlyA")
    TeamMembership.objects.create(team=team, user_id=member.id)
    WorkspaceTeam.objects.create(team=team, workspace=ws_a, access=WorkspaceAccess.MANAGE)
    assert effective_access(member, ws_a) == "manage"
    assert effective_access(member, ws_b) is None


@pytest.mark.django_db
def test_effective_access_none_workspace_is_none(demo_tenant):
    user = _mk_user("ea_nullws@example.com")
    _mk_membership(demo_tenant, user, OrgRole.OWNER)
    _activate(demo_tenant)
    assert effective_access(user, None) is None


@pytest.mark.django_db
def test_effective_access_member_is_query_bounded(demo_tenant, django_assert_max_num_queries):
    """rbac.md §7: effective_access must be O(1) queries per call (membership + teams +
    WorkspaceTeam) — NOT growing with team count."""
    member = _mk_user("ea_qcount@example.com")
    _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)
    ws = _mk_workspace()
    # Several teams, all linked to the same workspace — must NOT cause per-team queries.
    for i in range(4):
        t = Team.objects.create(name=f"qc-team-{i}")
        TeamMembership.objects.create(team=t, user_id=member.id)
        WorkspaceTeam.objects.create(team=t, workspace=ws, access=WorkspaceAccess.READ)
    # membership(public) + team-ids(tenant) + WorkspaceTeam(tenant) = a small constant.
    with django_assert_max_num_queries(3):
        assert effective_access(member, ws) == "read"


# ===========================================================================
# can() — workspace-scoped matrix (rbac.md §6)
# ===========================================================================
@pytest.mark.django_db
def test_can_workspace_actions_by_access_level(demo_tenant):
    """Each access level's allowed actions (rbac.md §3/§6 workspace + survey tables)."""
    reader = _mk_user("c_reader@example.com")
    writer = _mk_user("c_writer@example.com")
    manager = _mk_user("c_manager@example.com")
    for u in (reader, writer, manager):
        _mk_membership(demo_tenant, u, OrgRole.MEMBER)
    _activate(demo_tenant)
    ws = _mk_workspace()
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)

    t_r = Team.objects.create(name="R")
    t_w = Team.objects.create(name="W")
    t_m = Team.objects.create(name="M")
    TeamMembership.objects.create(team=t_r, user_id=reader.id)
    TeamMembership.objects.create(team=t_w, user_id=writer.id)
    TeamMembership.objects.create(team=t_m, user_id=manager.id)
    WorkspaceTeam.objects.create(team=t_r, workspace=ws, access=WorkspaceAccess.READ)
    WorkspaceTeam.objects.create(team=t_w, workspace=ws, access=WorkspaceAccess.WRITE)
    WorkspaceTeam.objects.create(team=t_m, workspace=ws, access=WorkspaceAccess.MANAGE)

    # read: view_* yes; mutate no.
    assert can(reader, "view_workspace", ws) is True
    assert can(reader, "view_survey", env) is True  # workspace_of(Environment) = ws
    assert can(reader, "create_survey", env) is False
    assert can(reader, "manage_workspace", ws) is False
    assert can(reader, "delete_survey", env) is False

    # write: read + create/edit/publish; NOT manage/delete_survey.
    assert can(writer, "view_workspace", ws) is True
    assert can(writer, "create_survey", env) is True
    assert can(writer, "edit_survey", env) is True
    assert can(writer, "publish_survey", env) is True
    assert can(writer, "delete_response", env) is True
    assert can(writer, "manage_workspace", ws) is False
    assert can(writer, "delete_survey", env) is False
    assert can(writer, "manage_environment", env) is False

    # manage: everything above + manage_workspace, delete_survey, manage_environment.
    assert can(manager, "manage_workspace", ws) is True
    assert can(manager, "delete_survey", env) is True
    assert can(manager, "manage_environment", env) is True
    assert can(manager, "create_survey", env) is True


@pytest.mark.django_db
def test_can_workspace_owner_admin_implicit_manage(demo_tenant):
    owner = _mk_user("c_owner@example.com")
    admin = _mk_user("c_admin@example.com")
    _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _mk_membership(demo_tenant, admin, OrgRole.ADMIN)
    _activate(demo_tenant)
    ws = _mk_workspace()
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    for u in (owner, admin):
        assert can(u, "delete_survey", env) is True
        assert can(u, "manage_workspace", ws) is True
        assert can(u, "view_workspace", ws) is True


@pytest.mark.django_db
def test_can_workspace_default_deny_non_member(demo_tenant):
    outsider = _mk_user("c_outsider@example.com")
    _activate(demo_tenant)
    ws = _mk_workspace()
    assert can(outsider, "view_workspace", ws) is False
    assert can(outsider, "delete_survey", ws) is False


@pytest.mark.django_db
def test_can_workspace_unreachable_obj_is_false(demo_tenant):
    """A workspace-scoped action with an obj that resolves to NO workspace → False (fail closed)."""
    owner = _mk_user("c_unreach@example.com")
    _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _activate(demo_tenant)
    # A plain object with no workspace path.
    assert can(owner, "view_survey", object()) is False
    assert can(owner, "view_survey", None) is False


# ===========================================================================
# can() — org-level matrix (rbac.md §6)
# ===========================================================================
@pytest.mark.django_db
def test_can_org_level_matrix(demo_tenant):
    owner = _mk_user("o_owner@example.com")
    admin = _mk_user("o_admin@example.com")
    member = _mk_user("o_member@example.com")
    _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _mk_membership(demo_tenant, admin, OrgRole.ADMIN)
    _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)

    # view_org / view_members: all members.
    for u in (owner, admin, member):
        assert can(u, "view_org") is True
        assert can(u, "view_members") is True

    # edit_org / invite_member / create_team / create_workspace / apikeys / webhook: owner+admin.
    for action in (
        "edit_org",
        "invite_member",
        "revoke_invitation",
        "create_team",
        "edit_team",
        "delete_team",
        "create_workspace",
        "delete_workspace",
        "grant_workspace_access",
        "view_apikey",
        "create_apikey",
        "revoke_apikey",
        "manage_webhook",
    ):
        assert can(owner, action) is True, action
        assert can(admin, action) is True, action
        assert can(member, action) is False, action

    # delete_org / grant_ownership: owner only.
    assert can(owner, "delete_org") is True
    assert can(admin, "delete_org") is False
    assert can(member, "delete_org") is False
    assert can(owner, "grant_ownership") is True
    assert can(admin, "grant_ownership") is False


@pytest.mark.django_db
def test_can_org_level_non_member_all_false(demo_tenant):
    outsider = _mk_user("o_outsider@example.com")
    _activate(demo_tenant)
    for action in ("view_org", "view_members", "edit_org", "create_team", "view_apikey"):
        assert can(outsider, action) is False, action


# ===========================================================================
# special predicates (rbac.md §2, §5, §6)
# ===========================================================================
@pytest.mark.django_db
def test_can_view_team_membership_scoped(demo_tenant):
    owner = _mk_user("vt_owner@example.com")
    member_in = _mk_user("vt_in@example.com")
    member_out = _mk_user("vt_out@example.com")
    _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _mk_membership(demo_tenant, member_in, OrgRole.MEMBER)
    _mk_membership(demo_tenant, member_out, OrgRole.MEMBER)
    _activate(demo_tenant)
    team = Team.objects.create(name="Marketing")
    TeamMembership.objects.create(team=team, user_id=member_in.id)

    assert can(owner, "view_team", team) is True  # owner always
    assert can(member_in, "view_team", team) is True  # in the team
    assert can(member_out, "view_team", team) is False  # member but not in this team


@pytest.mark.django_db
def test_can_manage_team_membership_team_admin(demo_tenant):
    owner = _mk_user("mt_owner@example.com")
    team_admin = _mk_user("mt_teamadmin@example.com")
    team_member = _mk_user("mt_teammember@example.com")
    other_member = _mk_user("mt_other@example.com")
    _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _mk_membership(demo_tenant, team_admin, OrgRole.MEMBER)  # org member, team admin
    _mk_membership(demo_tenant, team_member, OrgRole.MEMBER)
    _mk_membership(demo_tenant, other_member, OrgRole.MEMBER)
    _activate(demo_tenant)
    team = Team.objects.create(name="Squad")
    TeamMembership.objects.create(team=team, user_id=team_admin.id, team_role=TeamRole.ADMIN)
    TeamMembership.objects.create(team=team, user_id=team_member.id, team_role=TeamRole.MEMBER)

    assert can(owner, "manage_team_membership", team) is True  # org owner: any team
    assert can(team_admin, "manage_team_membership", team) is True  # team-admin of THIS team
    assert can(team_member, "manage_team_membership", team) is False  # plain team member
    assert can(other_member, "manage_team_membership", team) is False  # not in team at all


@pytest.mark.django_db
def test_can_manage_team_membership_team_admin_only_own_team(demo_tenant):
    """A team-admin of team A cannot manage team B's membership (rbac.md §5: THIS team only)."""
    team_admin = _mk_user("mto_admin@example.com")
    _mk_membership(demo_tenant, team_admin, OrgRole.MEMBER)
    _activate(demo_tenant)
    team_a = Team.objects.create(name="TeamA")
    team_b = Team.objects.create(name="TeamB")
    TeamMembership.objects.create(team=team_a, user_id=team_admin.id, team_role=TeamRole.ADMIN)
    assert can(team_admin, "manage_team_membership", team_a) is True
    assert can(team_admin, "manage_team_membership", team_b) is False


@pytest.mark.django_db
def test_can_change_member_role_admin_vs_owner_and_last_owner(demo_tenant):
    owner = _mk_user("cmr_owner@example.com")
    admin = _mk_user("cmr_admin@example.com")
    member = _mk_user("cmr_member@example.com")
    m_owner = _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _mk_membership(demo_tenant, admin, OrgRole.ADMIN)
    m_member = _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)

    # Admin CAN change a member's role.
    assert can(admin, "change_member_role", m_member) is True
    # Admin CANNOT change an owner's role.
    assert can(admin, "change_member_role", m_owner) is False
    # Owner CAN change another member.
    assert can(owner, "change_member_role", m_member) is True
    # Last-owner invariant: nobody can change the sole owner's role (would drop to 0 owners).
    assert can(owner, "change_member_role", m_owner) is False


@pytest.mark.django_db
def test_can_change_member_role_last_owner_ok_with_second_owner(demo_tenant):
    owner1 = _mk_user("lo_owner1@example.com")
    owner2 = _mk_user("lo_owner2@example.com")
    m1 = _mk_membership(demo_tenant, owner1, OrgRole.OWNER)
    _mk_membership(demo_tenant, owner2, OrgRole.OWNER)
    _activate(demo_tenant)
    # With a second active owner, changing owner1's role is allowed (another owner remains).
    assert can(owner1, "change_member_role", m1) is True


@pytest.mark.django_db
def test_can_remove_member_admin_vs_owner_and_last_owner(demo_tenant):
    owner = _mk_user("rm_owner@example.com")
    admin = _mk_user("rm_admin@example.com")
    member = _mk_user("rm_member@example.com")
    m_owner = _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    m_admin = _mk_membership(demo_tenant, admin, OrgRole.ADMIN)
    m_member = _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)

    assert can(admin, "remove_member", m_member) is True
    assert can(admin, "remove_member", m_owner) is False  # admin can't remove owner
    assert can(owner, "remove_member", m_admin) is True
    assert can(owner, "remove_member", m_owner) is False  # last owner
    # A plain member cannot remove anyone.
    assert can(member, "remove_member", m_admin) is False


# ===========================================================================
# fail-closed semantics (rbac.md §2, §7)
# ===========================================================================
@pytest.mark.django_db
def test_can_anonymous_user_always_false(demo_tenant):
    _activate(demo_tenant)
    ws = _mk_workspace()
    assert can(AnonymousUser(), "view_workspace", ws) is False
    assert can(None, "view_org") is False


@pytest.mark.django_db
def test_can_superuser_does_not_bypass(demo_tenant):
    """rbac.md §2: is_superuser bypasses RBAC only in Django admin, NOT can()."""
    connection.set_schema_to_public()
    User.objects.filter(email="super@example.com").delete()
    su = User.objects.create_superuser(email="super@example.com", password="x")
    # Superuser with NO membership in this org → denied everywhere by can().
    _activate(demo_tenant)
    ws = _mk_workspace()
    assert can(su, "view_org") is False
    assert can(su, "view_workspace", ws) is False
    assert can(su, "delete_survey", ws) is False


@pytest.mark.django_db
def test_can_unknown_action_raises_value_error(demo_tenant):
    user = _mk_user("ua_user@example.com")
    _mk_membership(demo_tenant, user, OrgRole.OWNER)
    _activate(demo_tenant)
    with pytest.raises(ValueError):
        can(user, "definitely_not_an_action")


# ===========================================================================
# workspace_of resolver (rbac.md §7)
# ===========================================================================
@pytest.mark.django_db
def test_workspace_of_resolves_environment(demo_tenant):
    _activate(demo_tenant)
    ws = _mk_workspace()
    env = Environment.objects.create(workspace=ws, type=EnvType.DEVELOPMENT)
    assert workspace_of(ws) == ws
    assert workspace_of(env) == ws
    assert workspace_of(None) is None
    assert workspace_of(object()) is None


# ===========================================================================
# {% can %} template assignment tag (rbac.md §8.3)
# ===========================================================================
def _render_can(user, action, obj_var=None, ctx=None):
    from django.template import engines

    dj = engines["django"]
    if obj_var is not None:
        src = '{% load rbac %}{% can u "' + action + '" obj as flag %}{{ flag }}'
    else:
        src = '{% load rbac %}{% can u "' + action + '" as flag %}{{ flag }}'
    context = {"u": user}
    if ctx:
        context.update(ctx)
    return dj.from_string(src).render(context)


@pytest.mark.django_db
def test_template_can_tag_delegates_to_can(demo_tenant):
    owner = _mk_user("tt_owner@example.com")
    member = _mk_user("tt_member@example.com")
    _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _mk_membership(demo_tenant, member, OrgRole.MEMBER)
    _activate(demo_tenant)
    ws = _mk_workspace()

    # Org action.
    assert _render_can(owner, "edit_org") == "True"
    assert _render_can(member, "edit_org") == "False"
    # Workspace action with an obj.
    assert _render_can(owner, "manage_workspace", obj_var="obj", ctx={"obj": ws}) == "True"
    assert _render_can(member, "manage_workspace", obj_var="obj", ctx={"obj": ws}) == "False"


def test_template_can_tag_anonymous_false():
    # No DB needed: anonymous → False (can() short-circuits before any query).
    assert _render_can(AnonymousUser(), "view_org") == "False"


@pytest.mark.django_db
def test_template_can_tag_unknown_action_is_false_not_500(demo_tenant):
    """A typo'd action in a template resolves to False (UI-only gate), not a 500/ValueError."""
    owner = _mk_user("tt_unknown@example.com")
    _mk_membership(demo_tenant, owner, OrgRole.OWNER)
    _activate(demo_tenant)
    assert _render_can(owner, "not_a_real_action") == "False"
