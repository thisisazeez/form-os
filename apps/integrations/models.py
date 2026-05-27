"""
integrations — TENANT schema (TENANT_APPS).

``SlackIntegration`` is a credential-free integration: the user pastes their Slack *Incoming
Webhook URL* (created in their own Slack workspace) — no OAuth, no app credentials from us. On a
subscribed survey/response event, the platform POSTs a Block-Kit message to that URL.

Field shape deliberately MIRRORS ``apps.webhooks.models.Webhook`` (environment FK, events,
enabled, created_by_user_id, and the four delivery-health fields) so the dispatch + delivery +
management surfaces stay symmetric and a reviewer can reason about one pattern. Differences:

  - ``incoming_webhook_url`` instead of ``url`` — the Slack-hosted endpoint (no signing; Slack
    incoming webhooks do not verify an HMAC, so there is no ``secret``).
  - ``survey_ids`` — JSON list of Survey ids this integration fires for. EMPTY list == ALL
    surveys in the environment (the common case). Stored as strings (cross-schema-safe UUIDs).
  - ``name`` — a human label for the destination (e.g. "#feedback channel").

``events`` reuses the FROZEN ``apps.webhooks.enums.WebhookEvent`` value set (response.created /
response.finished / survey.published / survey.closed) — NOT a fork. Adding events later remains
one coordinated change across the whole platform.

TENANT-schema model. FK(Environment) is tenant→tenant (OK). ``created_by_user_id`` is the
cross-schema User pattern (ADR-002 — no FK).
"""
from django.db import models

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel
from apps.workspaces.models import Environment


class SlackIntegration(TimeStampedUUIDModel):
    """A Slack Incoming-Webhook destination subscribed to survey/response events."""

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="slack_integrations",
    )
    # Human label for the destination (channel / workspace), shown in the management UI.
    name = models.CharField(max_length=255, blank=True, default="")
    # The Slack-hosted Incoming Webhook URL the user pasted (https://hooks.slack.com/services/...).
    incoming_webhook_url = models.URLField(max_length=2048)
    # Subscribed events — subset of WebhookEvent values (reused enum). Validated app-side.
    events = models.JSONField(default=list)
    # Survey ids (as strings) this integration fires for. EMPTY == all surveys in the env.
    survey_ids = models.JSONField(default=list)
    enabled = models.BooleanField(default=True)
    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True)

    # --- Delivery / failure tracking (mirrors Webhook) ---------------------------------------
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.IntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    consecutive_failures = models.PositiveIntegerField(default=0)

    objects = UserHydrationManager()

    class Meta:
        db_table = "integrations_slack"
        indexes = [
            models.Index(fields=["environment"], name="ix_slack_environment"),
        ]

    def __str__(self) -> str:
        return f"slack -> {self.name or self.incoming_webhook_url}"
