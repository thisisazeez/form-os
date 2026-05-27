"""
The caller's CURRENT (active) workspace within the routed org — the single source of truth
the whole admin app scopes to (dashboard / surveys / results / contacts / segments).

A user can belong to many orgs, and an org has many workspaces; the unified sidebar switcher
(templates/_layouts/app_shell.html) lets them pick one. We remember the choice per org in the
Django session and ALWAYS re-validate it against the caller's current access (RBAC): a stored
id the user can no longer read is ignored and we fall back to their first readable workspace.

Resolution is built on the N+1-free ``apps.workspaces.access`` helpers (no per-row ``can()``).
Returns ``None`` when the caller can read no workspace in the org (→ empty/locked surfaces,
default-deny, not a 403 — mirrors the existing ``_readable_environment_ids`` behavior).
"""
from __future__ import annotations


def _session_key(org) -> str:
    return f"current_workspace:{org.id}"


def current_workspace(request):
    """The caller's active ``Workspace`` in the routed org, or ``None``.

    Session-stored per org; falls back to the first readable workspace (ordered by name).
    Always RBAC-validated — a stored workspace the user can no longer read is discarded.
    """
    org = getattr(request, "tenant", None)
    user = getattr(request, "user", None)
    if org is None or user is None or not getattr(user, "is_authenticated", False):
        return None

    from apps.workspaces.access import readable_workspaces_with_access

    workspaces, _access = readable_workspaces_with_access(user)
    if not workspaces:
        return None

    stored = request.session.get(_session_key(org))
    if stored:
        for ws in workspaces:
            if str(ws.id) == str(stored):
                return ws
    return workspaces[0]  # default: first readable workspace (by name)


def set_current_workspace(request, workspace) -> None:
    """Remember ``workspace`` as the caller's current workspace for the routed org (session)."""
    org = getattr(request, "tenant", None)
    if org is not None and workspace is not None:
        request.session[_session_key(org)] = str(workspace.id)


def current_workspace_env_ids(request) -> list:
    """Environment ids of the caller's current workspace (what dashboard/surveys scope to).

    Empty list when there is no current workspace (non-member / no workspace access) — the
    list surfaces then render empty, exactly like the old all-readable-envs path did.
    """
    ws = current_workspace(request)
    if ws is None:
        return []
    from apps.workspaces.models import Environment

    return list(
        Environment.objects.filter(workspace=ws).values_list("id", flat=True)
    )
