"""
Workspace list-access helper — the N+1-free "which workspaces can this user read, and at
what level" computation (rbac.md §7 "O(1) queries", §8.1 "list endpoints filter, not 403").

This is the ONE place a list endpoint computes the caller's per-workspace access map. It does
NOT call ``can()`` / ``effective_access()`` per row (that would be the N+1 the contract
forbids). Instead it resolves the org membership once, then builds a
``{workspace_id: access}`` map with a bounded number of queries:

  - Org owner/admin → implicit ``manage`` on EVERY workspace (rbac.md §3): one query for all
    workspaces; map every id to ``"manage"``.
  - Org member → one query for the user's team ids (TeamMembership.user_id), one query for
    the WorkspaceTeam rows of those teams; fold to ``max(access)`` per workspace.
  - Non-member → empty.

Returns ``(workspaces_list, access_map)`` where ``workspaces_list`` is the readable
workspaces (those present in the map) and ``access_map`` is ``{id: "read"|"write"|"manage"}``.
"""
from __future__ import annotations

from apps.common.permissions import (
    ORG_IMPLICIT_MANAGE_ROLES,
    WORKSPACE_ACCESS_ORDER,
    active_org_membership,
)

from .models import Workspace


def workspace_access_map(user) -> dict:
    """Return ``{workspace_id: access}`` for every workspace the user can at least read.

    Bounded queries (no per-row can()): membership (public), then either one all-workspaces
    query (owner/admin) or team-ids + WorkspaceTeam (member).
    """
    membership = active_org_membership(user)
    if membership is None:
        return {}

    # Org owner/admin: implicit manage on every workspace (rbac.md §3).
    if membership.role in ORG_IMPLICIT_MANAGE_ROLES:
        return {
            ws_id: "manage"
            for ws_id in Workspace.objects.values_list("id", flat=True)
        }

    # Org member: max(access) over the user's teams' WorkspaceTeam rows.
    from apps.teams.models import TeamMembership, WorkspaceTeam

    team_ids = set(
        TeamMembership.objects.filter(user_id=user.id).values_list("team_id", flat=True)
    )
    if not team_ids:
        return {}

    access_map: dict = {}
    rows = WorkspaceTeam.objects.filter(team_id__in=team_ids).values_list(
        "workspace_id", "access"
    )
    for workspace_id, access in rows:
        rank = WORKSPACE_ACCESS_ORDER.get(access, 0)
        current = access_map.get(workspace_id)
        if current is None or rank > WORKSPACE_ACCESS_ORDER.get(current, 0):
            access_map[workspace_id] = access
    return access_map


def readable_workspaces_with_access(user):
    """Return ``(workspaces_list, access_map)`` filtered to the caller's readable workspaces.

    ``workspaces_list`` is ordered by name for a stable UI; ``access_map`` is the
    ``{id: access}`` map (also used to populate the ``effective_access`` serializer field
    without re-querying).
    """
    access_map = workspace_access_map(user)
    if not access_map:
        return [], {}
    workspaces = list(
        Workspace.objects.filter(id__in=access_map.keys()).order_by("name", "id")
    )
    return workspaces, access_map
