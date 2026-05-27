"""
Back-compat shim — the DRF permission classes now live in ``apps.common.permissions``.

The canonical home for the entire authorization surface is ``apps.common.permissions`` (the
enumerated-territory module that owns ``can()`` / ``effective_access()`` and the DRF adapter
classes — rbac.md §7: "exactly one authorization entry point"). This module re-exports them
so any ``from apps.common.drf_permissions import ...`` import keeps working; it contains NO
logic of its own. Prefer importing from ``apps.common.permissions`` directly.
"""
from __future__ import annotations

from .permissions import (  # noqa: F401
    CanPerform,
    CanPerformAction,
    IsOrgMember,
    IsOrgOwnerOrAdmin,
)

__all__ = ["IsOrgMember", "IsOrgOwnerOrAdmin", "CanPerform", "CanPerformAction"]
