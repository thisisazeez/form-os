"""
webhooks — TENANT schema (TENANT_APPS). Webhook (contracts/models.md §2.14). Model frozen
now; delivery endpoints are Phase 7.

TENANT-schema model. FK(Environment) is tenant→tenant (OK). `created_by_user_id` is the
cross-schema User pattern (ADR-002).

PLACEMENT NOTE (honored from the scaffold): models.md PART B groups Webhook + ApiKey under
an `integrations` app; the Phase-0 scaffold split them into `webhooks` + `apikeys`. This
honors the frozen scaffold placement — consolidating would be a coordinated re-broadcast
(PLAN §15), out of scope for the model owner.
"""
from django.db import models

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel
from apps.workspaces.models import Environment


class Webhook(TimeStampedUUIDModel):
    """Outbound webhook subscription (models.md §2.14)."""

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="webhooks",
    )
    url = models.URLField(max_length=2048)
    # Subscribed events — subset of WebhookEvent values (see enums.py). Validated app-side.
    # Callable default (mutable) per Django rules.
    events = models.JSONField(default=list)
    # HMAC signing secret (write-only in API; never returned in full — models.md §2.14).
    secret = models.CharField(max_length=128)
    enabled = models.BooleanField(default=True)
    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True)

    # --- Delivery / failure tracking (basic retry+failure recording, Wave 2 delivery). ---
    # Updated by the async delivery task after each attempt so admins can see endpoint health
    # in the management UI. consecutive_failures drives auto-disable after a failure ceiling.
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.IntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    consecutive_failures = models.PositiveIntegerField(default=0)

    objects = UserHydrationManager()

    class Meta:
        db_table = "webhooks_webhook"
        indexes = [
            models.Index(fields=["environment"], name="ix_webhook_environment"),
        ]

    def __str__(self) -> str:
        return f"webhook -> {self.url}"
