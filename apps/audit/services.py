"""
Audit-log write helper (TENANT schema) — the single ``record(...)`` entry point.

The append-only ``AuditLog`` (models.md §2.16) is written from the service/view layer on key
mutations (survey create/publish/delete, member invite/role-change, api-key create/revoke,
webhook create/delete). Every write goes through ``record()`` so the actor/IP/target shape is
uniform and one place owns the "never let auditing break the audited action" guarantee.

WHY a helper, not signals: the codebase has no Django-signal infrastructure (mutations live in
explicit service functions); calling ``record()`` at the end of each mutation matches the
existing pattern and keeps the write ordering deterministic.

CROSS-SCHEMA (ADR-002): ``actor_user_id`` is the PUBLIC ``User`` PK stored as a plain UUID (no
FK). ``AuditLog`` itself is tenant-schema — ``record()`` must be called with the tenant schema
ACTIVE (it is, on every authenticated admin request, after TenantSlugMiddleware activation).
"""
from __future__ import annotations

import logging

from .models import AuditLog

logger = logging.getLogger(__name__)

__all__ = ["record", "record_from_request"]

# Action string constants (kept here so callers don't drift on spelling). Free-form by design
# (models.md §2.16 stores `action` as a CharField), but centralizing avoids typos in filters.
ACTION_SURVEY_CREATED = "survey.created"
ACTION_SURVEY_PUBLISHED = "survey.published"
ACTION_SURVEY_CLOSED = "survey.closed"
ACTION_SURVEY_DELETED = "survey.deleted"
ACTION_MEMBER_INVITED = "member.invited"
ACTION_MEMBER_ROLE_CHANGED = "member.role_changed"
ACTION_APIKEY_CREATED = "apikey.created"
ACTION_APIKEY_REVOKED = "apikey.revoked"
ACTION_WEBHOOK_CREATED = "webhook.created"
ACTION_WEBHOOK_DELETED = "webhook.deleted"
ACTION_INTEGRATION_SLACK_CONNECTED = "integration.slack.connected"
ACTION_INTEGRATION_SLACK_UPDATED = "integration.slack.updated"
ACTION_INTEGRATION_SLACK_DISCONNECTED = "integration.slack.disconnected"


def record(
    *,
    action: str,
    target_type: str = "",
    target_id=None,
    actor_user_id=None,
    metadata: dict | None = None,
    ip: str | None = None,
) -> AuditLog | None:
    """Write one append-only ``AuditLog`` row. Best-effort: never raises into the caller.

    Auditing is a side effect of the real mutation — if the audit insert fails we log and
    return None rather than rolling back (or 500-ing) the action the user actually requested.
    Must be called with the tenant schema ACTIVE.
    """
    try:
        return AuditLog.objects.create(
            action=action,
            target_type=target_type or "",
            target_id=str(target_id) if target_id is not None else None,
            actor_user_id=actor_user_id,
            metadata=metadata or {},
            ip=ip,
        )
    except Exception:  # noqa: BLE001 — auditing must never break the audited action.
        logger.exception("Failed to write audit log for action=%s", action)
        return None


def record_from_request(
    request,
    *,
    action: str,
    target_type: str = "",
    target_id=None,
    metadata: dict | None = None,
) -> AuditLog | None:
    """``record()`` convenience that derives actor + IP from an authenticated ``request``.

    Resolves the actor from ``request.user`` (anonymous → system/null actor) and the client IP
    from the standard proxy headers (stored only when present; PII-gated on read — rbac.md §11).
    """
    user = getattr(request, "user", None)
    actor_user_id = getattr(user, "id", None) if getattr(user, "is_authenticated", False) else None
    return record(
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_user_id=actor_user_id,
        metadata=metadata,
        ip=_client_ip(request),
    )


def _client_ip(request) -> str | None:
    """First hop of X-Forwarded-For, then REMOTE_ADDR (mirrors responses.public_views)."""
    for header in ("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"):
        raw = request.META.get(header)
        if raw:
            return raw.split(",")[0].strip()
    return None
