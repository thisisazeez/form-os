"""
Environment scoping + PII-privilege helpers shared by the contacts and segments views.

These build on the FROZEN RBAC layer (apps.common.permissions.can / effective_access) and the
workspaces list-access helper (apps.workspaces.access.workspace_access_map) — they NEVER
re-implement authorization. Their job is the N+1-free "which environments can this caller
read, and at what level" mapping that env-scoped list endpoints (contacts, segments) need, plus
the PII unmask decision (rbac.md §11 — defined as "write-or-higher sees raw PII").

Why here (contacts app) and not apps.common: apps.common is OUT of this producer's territory
(use, don't modify). contacts + segments are both in-territory, and segments imports from
contacts already (the evaluator reads Contact models), so co-locating the shared scoping here
keeps it in-territory and dependency-clean.
"""
from __future__ import annotations

from django.http import Http404

from apps.common.permissions import WORKSPACE_ACCESS_ORDER, effective_access
from apps.workspaces.access import workspace_access_map
from apps.workspaces.models import Environment

# Access levels that may see raw PII values (rbac.md §11 privilege definition: write+).
WORKSPACE_WRITE_OR_HIGHER = frozenset(
    level for level, rank in WORKSPACE_ACCESS_ORDER.items() if rank >= WORKSPACE_ACCESS_ORDER["write"]
)


def get_environment_or_404(environment_id) -> Environment:
    """Load an ``Environment`` with its workspace (for workspace_of() — no extra query in
    can()). 404 if missing/malformed. Does NOT authorize — the caller runs ``can()``."""
    try:
        return Environment.objects.select_related("workspace").get(pk=environment_id)
    except (Environment.DoesNotExist, ValueError):
        raise Http404("Environment not found.") from None


def environment_access_map(user) -> dict:
    """Return ``{environment_id: access}`` for every environment whose workspace the caller can
    at least read. ONE bounded computation: the workspace access map (bounded queries) + one
    query mapping environments → their workspace.

    NO per-row can() (rbac.md §7 "O(1) queries"; §8.1 "list endpoints filter, not 403").
    """
    ws_access = workspace_access_map(user)
    if not ws_access:
        return {}
    rows = Environment.objects.filter(workspace_id__in=ws_access.keys()).values_list(
        "id", "workspace_id"
    )
    return {env_id: ws_access[ws_id] for env_id, ws_id in rows}


def readable_environment_ids(user) -> set:
    """The set of environment ids the caller can read (drives list filtering)."""
    return set(environment_access_map(user).keys())


def pii_unmasked_for_environment(user, environment) -> bool:
    """True if the caller may see RAW PII values for contacts in ``environment`` (rbac.md §11).

    Privilege = workspace access >= ``write`` (org owner/admin get implicit manage → True).
    Computed via effective_access on the environment's workspace (O(1) — single object).
    """
    level = effective_access(user, environment.workspace)
    return level in WORKSPACE_WRITE_OR_HIGHER
