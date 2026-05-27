"""
Slack message builder — turns a webhook event + payload into a Slack Incoming-Webhook JSON body.

Slack Incoming Webhooks accept a JSON body with a top-level ``text`` (the notification/fallback
string, also used in push notifications) and an optional ``blocks`` array (Block Kit) for the
rich in-channel rendering. We always send BOTH so the message reads well everywhere.

The payload shape is the one built by ``apps.webhooks.events`` (survey_payload / response_payload)
and passed unchanged through dispatch — compact ids + a few descriptive fields, never PII. We
keep this builder pure (event + payload -> dict) so it is trivially unit-testable and importable
without any delivery machinery.
"""
from __future__ import annotations

from apps.webhooks.enums import WebhookEvent

__all__ = ["build_slack_message", "event_label"]

# Friendly, human-readable headline per event (the Block-Kit section + fallback text lead).
_EVENT_HEADLINES = {
    WebhookEvent.SURVEY_PUBLISHED.value: ":rocket: Survey published",
    WebhookEvent.SURVEY_CLOSED.value: ":lock: Survey closed",
    WebhookEvent.RESPONSE_CREATED.value: ":pencil: New response started",
    WebhookEvent.RESPONSE_FINISHED.value: ":white_check_mark: Response completed",
}


def event_label(event: str) -> str:
    """Human label for an event value (falls back to the raw value if unknown)."""
    try:
        return WebhookEvent(event).label
    except ValueError:
        return event


def build_slack_message(event: str, payload: dict) -> dict:
    """Build the Slack Incoming-Webhook JSON body for ``event`` + ``payload``.

    Returns ``{"text": <fallback>, "blocks": [...]}``. ``payload`` is the webhook ``data`` dict
    (survey_payload for survey.* events, response_payload for response.* events).
    """
    headline = _EVENT_HEADLINES.get(event, f":bell: {event_label(event)}")

    # Descriptive context fields differ slightly between survey.* and response.* payloads.
    fields: list[dict] = []
    survey_name = payload.get("name")
    if survey_name:
        fields.append(_field("Survey", survey_name))

    status = payload.get("status")
    if status:
        fields.append(_field("Status", status))

    response_id = payload.get("response_id")
    if response_id:
        fields.append(_field("Response", response_id))
        finished = payload.get("finished")
        fields.append(_field("Finished", "Yes" if finished else "No"))
        language = payload.get("language")
        if language:
            fields.append(_field("Language", language))

    survey_id = payload.get("survey_id")
    if survey_id and not survey_name:
        fields.append(_field("Survey ID", survey_id))

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{headline}*"},
        }
    ]
    if fields:
        blocks.append({"type": "section", "fields": fields})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "Sent by *OpenSurvey* integrations"}
            ],
        }
    )

    # Fallback text — used in notifications and in clients that don't render blocks.
    fallback_bits = [headline.replace(":", "").strip()]
    if survey_name:
        fallback_bits.append(f"— {survey_name}")
    elif survey_id:
        fallback_bits.append(f"— survey {survey_id}")
    text = " ".join(fallback_bits)

    return {"text": text, "blocks": blocks}


def _field(label: str, value) -> dict:
    """A Block-Kit ``fields`` entry rendering ``*label:*\\nvalue`` as mrkdwn."""
    return {"type": "mrkdwn", "text": f"*{label}:*\n{value}"}
