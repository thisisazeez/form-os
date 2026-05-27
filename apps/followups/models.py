"""
followups — TENANT schema (TENANT_APPS). Per-(follow-up, response) delivery ledger.

The follow-up DEFINITIONS live on the Survey definition (``Survey.follow_ups`` JSON;
survey-schema.json #/$defs/followUp). This app records a DELIVERY ATTEMPT per
(follow_up_id, response) so the dashboard / "codes can be read from DB" (the product
owner's accepted email-mock) have a durable, queryable outcome row — mirroring how
``apps.webhooks`` records delivery health, but at the per-(follow-up, response) grain
(a follow-up fires once per finalized response, not N events over time).

TENANT-schema model: FK(Survey)/FK(Response) are tenant->tenant (OK). No cross-schema FK.

A ``unique(response, follow_up_id)`` constraint makes dispatch IDEMPOTENT: if finalize ever
re-fires for the same response, the service finds the existing row and skips re-enqueue, so a
respondent never gets a duplicate follow-up email.
"""
from django.db import models

from apps.common.models import TimeStampedUUIDModel
from apps.responses.models import Response
from apps.surveys.models import Survey

from .enums import FollowUpDeliveryStatus


class FollowUpDelivery(TimeStampedUUIDModel):
    """One follow-up send attempt for one finalized response (the FollowUpDelivery ledger).

    ``follow_up_id`` is the follow-up's id from ``Survey.follow_ups[].id`` (a JSON id, not a
    DB FK — the definitions live in JSON). ``recipient_email`` is the resolved address (blank
    when resolution found no email -> status SKIPPED). ``rendered_subject`` is stored so the
    dashboard / tests can read what was sent without re-rendering (the body is large + HTML,
    so we store only its presence via status, not the full HTML, keeping the ledger compact).
    """

    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="follow_up_deliveries",
    )
    response = models.ForeignKey(
        Response,
        on_delete=models.CASCADE,
        related_name="follow_up_deliveries",
    )
    # The follow-up's JSON id (Survey.follow_ups[].id). Not an FK — definitions live in JSON.
    follow_up_id = models.CharField(max_length=64)
    # Resolved recipient address; blank when no email could be resolved (status = skipped).
    recipient_email = models.EmailField(max_length=320, blank=True)
    # The recall-resolved, i18n-resolved subject actually rendered (auditable; tests read it).
    rendered_subject = models.CharField(max_length=998, blank=True)
    status = models.CharField(
        max_length=16,
        choices=FollowUpDeliveryStatus.choices,
        default=FollowUpDeliveryStatus.PENDING,
    )
    # Failure detail (transport/render error), truncated. Empty on success/skip.
    error = models.CharField(max_length=1000, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "followups_followupdelivery"
        constraints = [
            # Idempotency: one delivery row per (response, follow-up). Re-finalize -> no dup send.
            models.UniqueConstraint(
                fields=["response", "follow_up_id"],
                name="uniq_followup_delivery_per_response",
            ),
        ]
        indexes = [
            models.Index(fields=["survey"], name="ix_fudelivery_survey"),
            models.Index(fields=["response"], name="ix_fudelivery_response"),
            models.Index(fields=["status"], name="ix_fudelivery_status"),
        ]

    def __str__(self) -> str:
        return f"followup {self.follow_up_id} -> {self.recipient_email or '(no recipient)'} [{self.status}]"
