"""
Integrations service layer (TENANT schema) — Slack CRUD + event dispatch fan-out.

Two responsibilities, mirroring apps.webhooks.services:

  1. **CRUD** — ``create_slack_integration`` / ``update_slack_integration`` /
     ``delete_slack_integration`` (the management UI calls these).

  2. **Dispatch** — ``dispatch_event(event, payload)`` finds every enabled SlackIntegration in
     the ACTIVE tenant schema that is subscribed to ``event`` AND scoped to the event's survey
     (empty ``survey_ids`` == all surveys), then enqueues one async Slack delivery per match
     (apps.integrations.tasks.deliver_slack). The active schema name is captured and handed to
     the task so the worker re-activates it (cross-schema worker pattern).

This ``dispatch_event`` is invoked as a best-effort side effect FROM
``apps.webhooks.services.dispatch_event`` (the single existing event hub) — we do NOT add new
call sites in surveys/responses; we reuse the one dispatcher every event already flows through.

EVENTS reuse the FROZEN ``apps.webhooks.enums.WebhookEvent`` value set (not a fork).
"""
from __future__ import annotations

from django.db import connection

from apps.webhooks.enums import WebhookEvent

from .models import SlackIntegration

__all__ = [
    "create_slack_integration",
    "update_slack_integration",
    "delete_slack_integration",
    "dispatch_event",
    "valid_events",
]


def valid_events() -> list[str]:
    """The frozen set of subscribable event strings (shared WebhookEvent values)."""
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


def _clean_survey_ids(survey_ids) -> list[str]:
    """Normalize survey ids to a de-duplicated list of strings (empty == all surveys)."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for sid in survey_ids or []:
        if sid is None:
            continue
        s = str(sid).strip()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    return cleaned


def create_slack_integration(
    *,
    environment,
    incoming_webhook_url: str,
    events,
    name: str = "",
    survey_ids=None,
    enabled: bool = True,
    created_by_user_id=None,
):
    """Create a SlackIntegration for ``environment``. Must run with the tenant schema ACTIVE."""
    return SlackIntegration.objects.create(
        environment=environment,
        name=(name or "").strip(),
        incoming_webhook_url=incoming_webhook_url,
        events=_clean_events(events),
        survey_ids=_clean_survey_ids(survey_ids),
        enabled=enabled,
        created_by_user_id=created_by_user_id,
    )


def update_slack_integration(
    *,
    integration: SlackIntegration,
    incoming_webhook_url: str | None = None,
    events=None,
    name: str | None = None,
    survey_ids=None,
    enabled: bool | None = None,
):
    """Update a Slack integration's URL / events / scope / label / enabled flag."""
    update_fields = ["updated_at"]
    if incoming_webhook_url is not None:
        integration.incoming_webhook_url = incoming_webhook_url
        update_fields.append("incoming_webhook_url")
    if events is not None:
        integration.events = _clean_events(events)
        update_fields.append("events")
    if survey_ids is not None:
        integration.survey_ids = _clean_survey_ids(survey_ids)
        update_fields.append("survey_ids")
    if name is not None:
        integration.name = name.strip()
        update_fields.append("name")
    if enabled is not None:
        integration.enabled = enabled
        update_fields.append("enabled")
    integration.save(update_fields=update_fields)
    return integration


def delete_slack_integration(*, integration: SlackIntegration) -> None:
    """Hard-delete a Slack integration."""
    integration.delete()


def dispatch_event(*, event: str, payload: dict) -> int:
    """Enqueue a Slack delivery for every matching SlackIntegration in the active schema.

    A match is: ``enabled=True`` AND ``event`` in ``events`` AND the integration is scoped to
    the event's survey (``survey_ids`` empty == all surveys, else the payload's ``survey_id``
    must be listed). Returns the number of deliveries enqueued (0 if nothing matches).

    Best-effort: a dispatch failure must NEVER break the business action that fired the event,
    so any error is swallowed (this runs as a side effect off the webhook dispatcher).

    Must be called with the tenant schema ACTIVE (reads tenant-schema rows). The active schema
    name is captured and handed to the task so the worker can re-activate it.
    """
    if event not in WebhookEvent.values:
        return 0

    # Local import: keeps the surveys/responses call sites free of delivery machinery and
    # avoids any import-time cycle.
    from .tasks import deliver_slack

    schema_name = connection.schema_name
    survey_id = str(payload.get("survey_id")) if payload.get("survey_id") is not None else None

    count = 0
    try:
        integrations = list(
            SlackIntegration.objects.filter(
                enabled=True, events__contains=[event]
            ).values_list("id", "survey_ids")
        )
    except Exception:  # noqa: BLE001 — never break the triggering action.
        return 0

    for integration_id, survey_ids in integrations:
        # Survey scoping: empty list == all surveys; else the event's survey must be listed.
        if survey_ids:
            if survey_id is None or survey_id not in [str(s) for s in survey_ids]:
                continue
        deliver_slack.delay(
            schema_name=schema_name,
            integration_id=str(integration_id),
            event=event,
            payload=payload,
        )
        count += 1
    return count
