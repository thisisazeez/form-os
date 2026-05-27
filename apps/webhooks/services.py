"""
Webhook service layer (TENANT schema) — secret generation, CRUD, and event dispatch.

The single home for Webhook business logic. The management UI (apps.webhooks.views) and the
event-firing call sites (survey/response services) call these. Two responsibilities:

  1. **CRUD + secret** — ``create_webhook`` mints an HMAC signing secret (returned to the
     creator; never shown again in full). ``update_webhook`` / ``delete_webhook`` round it out.

  2. **Dispatch** — ``dispatch_event(event, payload)`` finds every enabled webhook in the ACTIVE
     tenant schema subscribed to ``event`` and enqueues an async delivery per endpoint
     (apps.webhooks.tasks.deliver_webhook). Because delivery runs on a Celery worker OUTSIDE the
     request's tenant context, we capture the current schema name and pass it to the task so the
     worker re-activates the right schema before recording the delivery result.

EVENTS are the FROZEN ``WebhookEvent`` value set (enums.py): response.created, response.finished,
survey.published, survey.closed. Adding events is a coordinated re-broadcast (out of scope).
"""
from __future__ import annotations

import secrets

from django.db import connection

from .enums import WebhookEvent
from .models import Webhook

__all__ = [
    "generate_secret",
    "create_webhook",
    "update_webhook",
    "delete_webhook",
    "dispatch_event",
    "valid_events",
]

_SECRET_BYTES = 32


def generate_secret() -> str:
    """Mint a fresh HMAC signing secret (64 hex chars). Used to sign every delivery payload."""
    return secrets.token_hex(_SECRET_BYTES)


def valid_events() -> list[str]:
    """The frozen set of subscribable event strings (WebhookEvent values)."""
    return list(WebhookEvent.values)


def _clean_events(events) -> list[str]:
    """Keep only known WebhookEvent values, de-duplicated, order-preserving."""
    allowed = set(WebhookEvent.values)
    seen: set[str] = set()
    cleaned: list[str] = []
    for ev in events or []:
        if ev in allowed and ev not in seen:
            seen.add(ev)
            cleaned.append(ev)
    return cleaned


def create_webhook(*, environment, url: str, events, enabled: bool = True, created_by_user_id=None):
    """Create a Webhook for ``environment`` with a freshly minted signing secret.

    Returns the Webhook (its ``secret`` is readable on the instance so the caller can show it
    once). Must be called with the tenant schema ACTIVE.
    """
    return Webhook.objects.create(
        environment=environment,
        url=url,
        events=_clean_events(events),
        secret=generate_secret(),
        enabled=enabled,
        created_by_user_id=created_by_user_id,
    )


def update_webhook(*, webhook: Webhook, url: str | None = None, events=None, enabled: bool | None = None):
    """Update a webhook's URL / subscribed events / enabled flag (secret is immutable here)."""
    update_fields = ["updated_at"]
    if url is not None:
        webhook.url = url
        update_fields.append("url")
    if events is not None:
        webhook.events = _clean_events(events)
        update_fields.append("events")
    if enabled is not None:
        webhook.enabled = enabled
        update_fields.append("enabled")
    webhook.save(update_fields=update_fields)
    return webhook


def delete_webhook(*, webhook: Webhook) -> None:
    """Hard-delete a webhook subscription."""
    webhook.delete()


def dispatch_event(*, event: str, payload: dict) -> int:
    """Enqueue a delivery for every enabled webhook subscribed to ``event`` in the active schema.

    Returns the number of deliveries enqueued (0 if nothing is subscribed). Best-effort: a
    dispatch failure must never break the business action that triggered the event, so any
    error is swallowed (the caller fires this as a side effect).

    Must be called with the tenant schema ACTIVE (it reads tenant-schema Webhook rows). The
    active schema name is captured and handed to the task so the worker can re-activate it.
    """
    if event not in WebhookEvent.values:
        return 0

    # Local import: avoids a circular import (tasks imports nothing heavy, but keep symmetry).
    from .tasks import deliver_webhook

    schema_name = connection.schema_name
    count = 0
    try:
        webhook_ids = list(
            Webhook.objects.filter(enabled=True, events__contains=[event]).values_list(
                "id", flat=True
            )
        )
    except Exception:  # noqa: BLE001 — never break the audited/triggering action.
        return 0

    for webhook_id in webhook_ids:
        deliver_webhook.delay(
            schema_name=schema_name,
            webhook_id=str(webhook_id),
            event=event,
            payload=payload,
        )
        count += 1

    # Fan out the SAME event to credential-free integrations (Slack) so the integrations hub
    # reuses this single dispatcher rather than adding its own call sites in surveys/responses.
    # Best-effort: an integrations problem must never break webhook dispatch or the triggering
    # business action. Local import avoids any app-loading cycle.
    try:
        from apps.integrations.services import dispatch_event as dispatch_integrations

        dispatch_integrations(event=event, payload=payload)
    except Exception:  # noqa: BLE001 — never break webhook dispatch / the triggering action.
        pass

    return count
