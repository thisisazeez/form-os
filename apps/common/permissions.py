"""
Authorization entry point — ``can(user, action, obj)`` (ADR-006 / rbac.md §7).

rbac.md §7 names this file the single source of truth for authorization: "there is exactly
one authorization entry point — can(user, action, obj). Every surface (DRF, HTMX, templates)
calls it. No surface invents its own checks."

This module implements the FROZEN signatures (``effective_access`` + ``can``) against the
rbac.md §4 effective-permission rule and the §6 capability×role/access matrix.

THE TWO LAYERS (rbac.md §1, kept DISTINCT — never collapsed):
  1. ORG role (public ``organizations.Membership.role`` ∈ owner|admin|member) — governs
     org-wide capabilities AND grants org owner/admin an IMPLICIT ``manage`` on every
     workspace.
  2. WORKSPACE access via Teams (tenant schema) — a ``Team`` (users via
     ``TeamMembership.user_id``) is granted ``WorkspaceTeam.access`` ∈ read|write|manage on a
     ``Workspace``. Governs per-workspace resource ops.

CROSS-SCHEMA RULE (ADR-002, rbac.md §4 note): ``Membership`` is PUBLIC-schema; ``Team`` /
``TeamMembership`` / ``WorkspaceTeam`` are TENANT-schema. We read the org ``Membership`` under
``schema_context(public)`` and the tenant team rows in the active tenant schema, matching
``TeamMembership.user_id`` to the public ``user.id`` by value (no cross-schema join/FK).

FAIL-CLOSED: anonymous → False; unknown action → ValueError; unreachable workspace → False.
``is_superuser`` is IGNORED here (rbac.md §2 — superuser bypasses RBAC only in Django admin,
never in the tenant API/HTMX/template surfaces).

This module also hosts the DRF permission classes (the API enforcement surface, rbac.md §8.1)
at the bottom — ``CanPerform`` / ``CanPerformAction`` (thin adapters that delegate to
``can()``), plus a re-export of the Wave-2a org-membership gates (``IsOrgMember`` /
``IsOrgOwnerOrAdmin``). They live alongside ``can()`` so the single authorization source is
one module (rbac.md §7: "there is exactly one authorization entry point").
"""
from __future__ import annotations

from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.permissions import BasePermission

# Re-export the Wave-2a org-membership gates (read-only integration — NOT redefined here).
from apps.organizations.permissions import IsOrgMember, IsOrgOwnerOrAdmin  # noqa: F401

# Workspace access ordering (rbac.md §3): read < write < manage.
WORKSPACE_ACCESS_ORDER = {"read": 1, "write": 2, "manage": 3}

# Org-role constants (mirror apps.organizations.enums.OrgRole — read-only integration).
_OWNER = "owner"
_ADMIN = "admin"
_MEMBER = "member"

# The org roles that get IMPLICIT manage on every workspace (rbac.md §3). Public so the
# list-access helper (apps/workspaces/access.py) can reuse the rule without importing privates.
ORG_IMPLICIT_MANAGE_ROLES = frozenset({_OWNER, _ADMIN})

# ---------------------------------------------------------------------------
# Action → requirement map (rbac.md §6, the authoritative matrix).
#
# Each action maps to ONE of:
#   ("org", {allowed roles})         — org-level; satisfied by Membership.role membership.
#   ("ws", "read"|"write"|"manage")  — workspace-level; satisfied by effective_access ≥ level.
#   ("special", "<predicate-name>")  — dedicated predicate (last-owner, team-admin, ownership).
#
# Unmapped action → ValueError (fail closed; never silently allow — rbac.md §7).
# ---------------------------------------------------------------------------
REQUIRED: dict[str, tuple[str, object]] = {
    # --- Org settings & lifecycle (public schema) ---
    "view_org": ("org", {_OWNER, _ADMIN, _MEMBER}),
    "edit_org": ("org", {_OWNER, _ADMIN}),
    "delete_org": ("org", {_OWNER}),
    # --- Members & Invitations (public schema) ---
    "view_members": ("org", {_OWNER, _ADMIN, _MEMBER}),
    "invite_member": ("org", {_OWNER, _ADMIN}),
    "revoke_invitation": ("org", {_OWNER, _ADMIN}),
    "change_member_role": ("special", "change_member_role"),
    "remove_member": ("special", "remove_member"),
    "grant_ownership": ("org", {_OWNER}),
    # --- Teams (tenant schema) ---
    "view_team": ("special", "view_team"),
    "create_team": ("org", {_OWNER, _ADMIN}),
    "edit_team": ("org", {_OWNER, _ADMIN}),
    "delete_team": ("org", {_OWNER, _ADMIN}),
    "manage_team_membership": ("special", "manage_team_membership"),
    "grant_workspace_access": ("org", {_OWNER, _ADMIN}),
    # --- Workspaces & Environments (tenant schema) ---
    "view_workspace": ("ws", "read"),
    "create_workspace": ("org", {_OWNER, _ADMIN}),
    "manage_workspace": ("ws", "manage"),
    "delete_workspace": ("org", {_OWNER, _ADMIN}),
    "manage_environment": ("ws", "manage"),
    # --- Surveys (tenant schema) ---
    "view_survey": ("ws", "read"),
    "create_survey": ("ws", "write"),
    "edit_survey": ("ws", "write"),
    "publish_survey": ("ws", "write"),
    "pause_survey": ("ws", "write"),
    "close_survey": ("ws", "write"),
    "delete_survey": ("ws", "manage"),
    # --- Actions / Events (tenant schema) — website/app-survey triggers (FEATURE_PARITY WS-11). ---
    # ActionClass is workspace-scoped via .environment.workspace (workspace_of()). Viewing is
    # workspace `read`; mutating (create/edit/delete) is workspace `write` (mirrors surveys'
    # create/edit — actions are survey-trigger config, not a destructive `manage` op).
    "view_action_class": ("ws", "read"),
    "create_action_class": ("ws", "write"),
    "edit_action_class": ("ws", "write"),
    "delete_action_class": ("ws", "write"),
    # --- Responses (tenant schema) ---
    "view_response": ("ws", "read"),
    "view_analytics": ("ws", "read"),
    "tag_response": ("ws", "write"),
    "delete_response": ("ws", "write"),
    "export_responses": ("ws", "read"),
    # --- Contacts & Segments (tenant schema) ---
    "view_contact": ("ws", "read"),
    "view_segment": ("ws", "read"),
    "create_contact": ("ws", "write"),
    "edit_contact": ("ws", "write"),
    "delete_contact": ("ws", "write"),
    "create_segment": ("ws", "write"),
    "edit_segment": ("ws", "write"),
    "delete_segment": ("ws", "write"),
    # --- Integrations (tenant schema) ---
    "view_apikey": ("org", {_OWNER, _ADMIN}),
    "create_apikey": ("org", {_OWNER, _ADMIN}),
    "revoke_apikey": ("org", {_OWNER, _ADMIN}),
    "manage_webhook": ("org", {_OWNER, _ADMIN}),
    "manage_integration": ("org", {_OWNER, _ADMIN}),
    # --- Audit log (tenant schema) — viewing the append-only log is an admin surface. ---
    "view_audit_log": ("org", {_OWNER, _ADMIN}),
}


# ---------------------------------------------------------------------------
# workspace_of(obj) — resolve the owning Workspace for a workspace-scoped target (rbac.md §7).
# ---------------------------------------------------------------------------
def workspace_of(obj):
    """Resolve the owning ``Workspace`` from a workspace-scoped target instance.

    Per rbac.md §7:
      Workspace → itself; Environment → .workspace; Survey → .environment.workspace;
      Response/Display/SurveyLink → .survey.environment.workspace;
      Contact/ContactAttributeKey/Segment → .environment.workspace.

    Resolution is structural (attribute walk), so it works for any model in those families
    without importing across-territory apps. Returns None if no Workspace is reachable
    (→ can() fails closed).
    """
    if obj is None:
        return None

    # Local import (avoid app-loading order issues; workspaces is in this producer's territory).
    from apps.workspaces.models import Workspace

    if isinstance(obj, Workspace):
        return obj

    # Environment / anything carrying `.workspace` directly.
    workspace = getattr(obj, "workspace", None)
    if isinstance(workspace, Workspace):
        return workspace

    # Survey / Contact / ContactAttributeKey / Segment → `.environment.workspace`.
    environment = getattr(obj, "environment", None)
    if environment is not None:
        ws = getattr(environment, "workspace", None)
        if isinstance(ws, Workspace):
            return ws

    # Response / Display / SurveyLink → `.survey.environment.workspace`.
    survey = getattr(obj, "survey", None)
    if survey is not None:
        env = getattr(survey, "environment", None)
        if env is not None:
            ws = getattr(env, "workspace", None)
            if isinstance(ws, Workspace):
                return ws

    return None


# ---------------------------------------------------------------------------
# Org membership resolution (public schema) — mirrors organizations.permissions.
# ---------------------------------------------------------------------------
def _active_org_membership(user):
    """The user's ACTIVE ``Membership`` for the CURRENTLY-ACTIVATED org, or None.

    rbac.md §10: authz runs AFTER tenant activation; ``can()`` always evaluates against the
    routed org. We resolve the org from the active tenant schema's ``connection.tenant`` and
    read ``Membership`` from the PUBLIC schema (it's a public-schema model — ADR-002).

    Returns None if: no authenticated user, no real tenant active (public schema), or the
    user has no active membership in that org (→ non-member, deny-all).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    from django.db import connection

    org = getattr(connection, "tenant", None)
    if org is None:
        return None
    public_schema = get_public_schema_name()
    # The public "tenant" row is not a real org.
    if getattr(org, "schema_name", None) == public_schema:
        return None

    from apps.organizations.enums import MembershipStatus
    from apps.organizations.models import Membership

    with schema_context(public_schema):
        return (
            Membership.objects.filter(
                user=user, organization=org, status=MembershipStatus.ACTIVE
            )
            .only("id", "role", "user_id", "organization_id", "status")
            .first()
        )


# Public alias: the canonical "resolve the caller's active org Membership for the routed org"
# helper, reused by the list-access helper (apps/workspaces/access.py) and the org-level
# surfaces. Same object as the internal name.
active_org_membership = _active_org_membership


def _team_ids_for_user(user) -> set:
    """The set of tenant ``Team`` ids the user belongs to (via ``TeamMembership.user_id``).

    ONE tenant-schema query. Matches ``TeamMembership.user_id`` to the public ``user.id`` by
    value — the canonical cross-schema read (rbac.md §4 note; no FK, no join).
    """
    from apps.teams.models import TeamMembership

    return set(
        TeamMembership.objects.filter(user_id=user.id).values_list("team_id", flat=True)
    )


# ---------------------------------------------------------------------------
# effective_access (rbac.md §4, FROZEN).
# ---------------------------------------------------------------------------
def effective_access(user, workspace):
    """Resolve a user's effective access to a workspace (rbac.md §4).

    Returns one of {None, "read", "write", "manage"}:
      - Org owner/admin → "manage" (implicit, short-circuit; never stored).
      - No active Membership for the current org → None (not a member; deny all).
      - Else max(WorkspaceTeam.access) over the user's teams linked to the workspace, via
        TeamMembership.user_id (cross-schema value match — no join). None if no rows.

    O(1) queries: at most one membership lookup (public), one team-id lookup (tenant), one
    WorkspaceTeam lookup (tenant). List endpoints MUST NOT call this per-row — they build a
    {workspace_id: access} map once (see apps/workspaces/views.py).
    """
    if workspace is None:
        return None

    membership = _active_org_membership(user)
    if membership is None:
        return None
    if membership.role in (_OWNER, _ADMIN):
        return "manage"

    # Org member: max(access) over the user's teams linked to this workspace.
    team_ids = _team_ids_for_user(user)
    if not team_ids:
        return None

    from apps.teams.models import WorkspaceTeam

    levels = WorkspaceTeam.objects.filter(
        team_id__in=team_ids, workspace=workspace
    ).values_list("access", flat=True)

    best = None
    best_rank = 0
    for access in levels:
        rank = WORKSPACE_ACCESS_ORDER.get(access, 0)
        if rank > best_rank:
            best_rank = rank
            best = access
    return best


def _meets(level: str | None, required: str) -> bool:
    """True if effective ``level`` satisfies the ``required`` workspace level (ordered)."""
    if level is None:
        return False
    return WORKSPACE_ACCESS_ORDER.get(level, 0) >= WORKSPACE_ACCESS_ORDER[required]


# ---------------------------------------------------------------------------
# Special-action predicates (rbac.md §2, §5, §6).
# ---------------------------------------------------------------------------
def _other_active_owner_exists(organization, *, exclude_user_id) -> bool:
    """True if the org has an active owner OTHER than ``exclude_user_id``.

    Unlocked read — sufficient for can()'s display-time check (rbac.md §2 footnote: can()
    returns False if the action would remove/demote the last owner). The DECISIVE,
    race-safe check is the locked re-count in apps.organizations.services (Wave 2a); can()
    is a fail-fast gate, not the concurrency guard.
    """
    from apps.organizations.enums import MembershipStatus, OrgRole
    from apps.organizations.models import Membership

    with schema_context(get_public_schema_name()):
        return (
            Membership.objects.filter(
                organization=organization,
                role=OrgRole.OWNER,
                status=MembershipStatus.ACTIVE,
            )
            .exclude(user_id=exclude_user_id)
            .exists()
        )


def _can_change_member_role(actor_membership, target_membership) -> bool:
    """change_member_role (rbac.md §2 + §6 members table).

    - Actor must be org owner/admin.
    - Admins cannot change an OWNER's role (only owners manage owners).
    - Demoting the last active owner is refused (last-owner invariant).
    (Granting OWNER is owner-only; that is the separate ``grant_ownership`` action — the
    serializer/service calls can(grant_ownership) when new_role==owner. Here we gate the
    *act* of changing a role at all.)
    """
    if actor_membership is None or actor_membership.role not in (_OWNER, _ADMIN):
        return False
    if target_membership is None:
        return False
    actor_is_owner = actor_membership.role == _OWNER
    # Admins cannot act on an owner.
    if target_membership.role == _OWNER and not actor_is_owner:
        return False
    # Last-owner invariant: demoting an owner needs another active owner to remain.
    if target_membership.role == _OWNER and not _other_active_owner_exists(
        target_membership.organization, exclude_user_id=target_membership.user_id
    ):
        return False
    return True


def _can_remove_member(actor_membership, target_membership) -> bool:
    """remove_member (rbac.md §2 + §6 members table).

    - Actor must be org owner/admin.
    - Admins cannot remove an OWNER.
    - Removing the last active owner is refused (last-owner invariant).
    """
    if actor_membership is None or actor_membership.role not in (_OWNER, _ADMIN):
        return False
    if target_membership is None:
        return False
    actor_is_owner = actor_membership.role == _OWNER
    if target_membership.role == _OWNER and not actor_is_owner:
        return False
    if target_membership.role == _OWNER and not _other_active_owner_exists(
        target_membership.organization, exclude_user_id=target_membership.user_id
    ):
        return False
    return True


def _can_view_team(user, membership, team) -> bool:
    """view_team (rbac.md §6 teams table): owner/admin always; org member only if in it."""
    if membership is None:
        return False
    if membership.role in (_OWNER, _ADMIN):
        return True
    # Org member: visible only if they are a member of THIS team.
    if team is None:
        return False
    from apps.teams.models import TeamMembership

    return TeamMembership.objects.filter(team=team, user_id=user.id).exists()


def _can_manage_team_membership(user, membership, team) -> bool:
    """manage_team_membership (rbac.md §5 + §6): org owner/admin (any team), OR team-admin
    of THIS specific team (delegated membership management; does NOT grant workspace access).
    """
    if membership is None:
        return False
    if membership.role in (_OWNER, _ADMIN):
        return True
    if team is None:
        return False
    from apps.teams.enums import TeamRole
    from apps.teams.models import TeamMembership

    return TeamMembership.objects.filter(
        team=team, user_id=user.id, team_role=TeamRole.ADMIN
    ).exists()


# ---------------------------------------------------------------------------
# can() — the single authorization predicate (rbac.md §7, FROZEN).
# ---------------------------------------------------------------------------
def can(user, action: str, obj=None) -> bool:
    """The single authorization predicate (rbac.md §7).

    Inputs (frozen):
      - user:   authenticated public User (AnonymousUser/None → always False).
      - action: a string from the rbac.md §6 matrix. Unknown action → ValueError (fail
                closed; never silently allow).
      - obj:    target instance, or None for collection/org-level actions. For
                workspace-scoped actions, the owning Workspace is reached via workspace_of(obj).

    Algorithm (rbac.md §7): anonymous → False; look up the action's requirement (org-level
    set | workspace level | special predicate); evaluate; fail closed.

    NOTE: ``is_superuser`` is intentionally NOT consulted (rbac.md §2 — admin-site only).
    """
    # 1. Anonymous / None → False (never raise).
    if user is None or not getattr(user, "is_authenticated", False):
        return False

    # 2. Unknown action → ValueError (fail closed; surface bugs).
    try:
        kind, requirement = REQUIRED[action]
    except KeyError as exc:
        raise ValueError(f"Unknown action '{action}' (not in rbac.md §6 matrix).") from exc

    # 3. Org-level action: caller's Membership.role must be in the allowed set.
    if kind == "org":
        membership = _active_org_membership(user)
        if membership is None:
            return False
        return membership.role in requirement

    # 4. Workspace-level action: effective_access(workspace_of(obj)) ≥ required level.
    if kind == "ws":
        workspace = workspace_of(obj)
        if workspace is None:
            return False  # unreachable workspace → fail closed.
        return _meets(effective_access(user, workspace), requirement)

    # 5. Special actions run their dedicated predicate.
    if kind == "special":
        membership = _active_org_membership(user)
        if membership is None:
            return False
        if requirement == "change_member_role":
            return _can_change_member_role(membership, obj)
        if requirement == "remove_member":
            return _can_remove_member(membership, obj)
        if requirement == "view_team":
            return _can_view_team(user, membership, obj)
        if requirement == "manage_team_membership":
            return _can_manage_team_membership(user, membership, obj)

    # 6. Fail closed.
    return False


# ===========================================================================
# DRF permission classes — the API enforcement surface (rbac.md §8.1).
#
# All authorization delegates to can() above (the single entry point). These classes are the
# thin DRF adapter. IsOrgMember/IsOrgOwnerOrAdmin are re-exported from
# apps.organizations.permissions at the top of this module (read-only integration).
#
# LIST endpoints FILTER, not 403 (rbac.md §8.1): get_queryset() returns only readable objects.
# These classes gate MUTATIONS and DETAIL reads; the filtering lives in the view
# (apps/workspaces/access.py builds the {workspace_id: access} map once — no per-row can()).
# ===========================================================================
class CanPerform(BasePermission):
    """Object-level permission for a FIXED rbac action: ``can(user, action, obj)``.

    Usage:
        permission_classes = [IsAuthenticated, IsOrgMember, CanPerform.for_action("delete_survey")]

    ``has_permission`` returns True (collection-level access is gated by IsOrgMember + the
    view's queryset filtering); ``has_object_permission`` runs the real per-object check.
    """

    action: str = ""

    @classmethod
    def for_action(cls, action: str):
        """Build a CanPerform subclass bound to ``action`` (DRF instantiates permission
        classes with no args, so we bind via a subclass attribute)."""
        return type(f"CanPerform_{action}", (cls,), {"action": action})

    def has_permission(self, request, view) -> bool:
        return True

    def has_object_permission(self, request, view, obj) -> bool:
        return can(request.user, self.action, obj)


class CanPerformAction(BasePermission):
    """ViewSet-aware permission driven by ``view.permission_action_map`` (rbac.md §8.1).

    Declare on the ViewSet:
        permission_action_map = {
            "list": "view_workspace", "retrieve": "view_workspace",
            "create": "create_workspace", "partial_update": "manage_workspace",
            "destroy": "delete_workspace",
        }

    - ``has_permission``: for collection/create actions (no object yet) calls
      ``can(user, action, None)`` so org-level requirements (e.g. create_workspace) are
      enforced before a body is processed. For object actions it returns True and lets
      ``has_object_permission`` do the per-object check (DRF calls both).
    - ``has_object_permission``: ``can(user, mapped_action, obj)``.

    A DRF action with NO entry in the map is DENIED (fail closed — every mutating endpoint must
    be mapped; reviewers verify this, rbac.md §8.1).
    """

    _OBJECT_ACTIONS = frozenset({"retrieve", "update", "partial_update", "destroy"})

    def _mapped_action(self, view) -> str | None:
        action_map = getattr(view, "permission_action_map", None) or {}
        drf_action = getattr(view, "action", None)
        if drf_action is None:
            return None
        return action_map.get(drf_action)

    def has_permission(self, request, view) -> bool:
        rbac_action = self._mapped_action(view)
        if rbac_action is None:
            return False  # unmapped action → fail closed.
        if getattr(view, "action", None) in self._OBJECT_ACTIONS:
            # Real check happens in has_object_permission; allow DRF to fetch the object.
            return True
        return can(request.user, rbac_action, None)

    def has_object_permission(self, request, view, obj) -> bool:
        rbac_action = self._mapped_action(view)
        if rbac_action is None:
            return False
        return can(request.user, rbac_action, obj)
