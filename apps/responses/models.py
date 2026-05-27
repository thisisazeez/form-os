"""
responses — TENANT schema (TENANT_APPS). One row per response (contracts/models.md §2.7–§2.9).

TENANT-schema models. FK(Survey)/FK(Response)/FK(Tag) are tenant→tenant (OK).

`contact_id` is a UUIDField, NOT an FK (ADR-008) — even though Contact is same-schema:
most responses are anonymous (null), and GDPR contact-erasure must ANONYMIZE responses
(null the contact_id) rather than cascade-delete them. Documented so reviewers don't "fix"
it into an FK. `created_by_user_id` is the cross-schema User pattern (ADR-002).
"""
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models.functions import Lower

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel, UUIDPrimaryKeyMixin
from apps.surveys.models import Survey


class Response(TimeStampedUUIDModel):
    """One response (models.md §2.7). `data` is JSONB keyed by question id → answer, with a
    GIN index for answer/attribute filtering (PLAN §7/§10)."""

    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    # JSONB keyed by question id → answer (shape per survey-schema.json §answer-shapes).
    data = models.JSONField(default=dict)
    # False while partial; True after the finalize POST.
    finished = models.BooleanField(default=False)
    # By-id reference to tenant Contact (nullable = anonymous). NOT an FK (ADR-008). Indexed.
    contact_id = models.UUIDField(null=True, blank=True)
    # Authenticated respondent (rare; usually null). NO FK (cross-schema, ADR-002).
    created_by_user_id = models.UUIDField(null=True, blank=True)
    # Language code the response was taken in.
    language = models.CharField(max_length=10, default="default")
    # {url, source, userAgent, ip, deviceType, country?}. meta.ip is PII (rbac.md §11) —
    # may be truncated/hashed per config.
    meta = models.JSONField(default=dict)
    # If the survey is single-use, the consumed token (also tracked on SurveyLink, §2.7a).
    single_use_token = models.CharField(max_length=64, null=True, blank=True)
    # = created_at semantics; explicit for funnel analytics.
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # Survey-quota enforcement (apps.surveys quotas; set at finalize). When this finished response
    # was the (limit+1)th matching one for some quota, `over_quota` is True and
    # `quota_full_ending_id` carries the FIRST matching+full quota's `endingId` (or None when that
    # quota declared no endingId — the runtime then shows a built-in 'quota full' screen). A normal
    # (not-over-quota) response keeps `over_quota=False` + `quota_full_ending_id=None`. Persisted
    # (not derived) so the state is queryable for analytics + survives a re-finalize idempotently.
    over_quota = models.BooleanField(default=False)
    quota_full_ending_id = models.CharField(max_length=64, null=True, blank=True)
    # `created_at` + `updated_at` come from TimeStampedUUIDModel; updated_at bumps on each
    # partial PATCH (models.md §2.7).

    objects = UserHydrationManager()

    class Meta:
        db_table = "responses_response"
        indexes = [
            # Answer/attribute filtering (PLAN §7/§10) — the load-bearing GIN index.
            GinIndex(fields=["data"], name="gin_response_data"),
            models.Index(fields=["survey"], name="ix_response_survey"),
            # Completion funnel / counts.
            models.Index(fields=["survey", "finished"], name="ix_response_survey_finished"),
            models.Index(fields=["contact_id"], name="ix_response_contact"),
            # Time-series analytics.
            models.Index(fields=["created_at"], name="ix_response_created"),
        ]

    def __str__(self) -> str:
        return f"response {self.id} ({'finished' if self.finished else 'partial'})"


class SurveyLink(TimeStampedUUIDModel):
    """Backs single-use / per-contact personalized links (models.md §2.7a). Only materialized
    when a survey needs trackable/consumable links; open public links don't need a row."""

    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="links",
    )
    token = models.CharField(max_length=64, unique=True)
    # Personalize/prefill for a specific contact (null = generic single-use). NOT an FK (ADR-008).
    contact_id = models.UUIDField(null=True, blank=True)
    # Set when the link's response is finalized (single-use).
    consumed_at = models.DateTimeField(null=True, blank=True)
    response = models.ForeignKey(
        Response,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="link",
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "responses_surveylink"
        indexes = [
            models.Index(fields=["survey", "contact_id"], name="ix_surveylink_survey_contact"),
        ]

    def __str__(self) -> str:
        return f"link {self.token[:8]}… -> survey {self.survey_id}"


class Display(UUIDPrimaryKeyMixin):
    """Impression tracking (models.md §2.8) — powers the view→start→finish funnel.

    Append-only impression: ONLY `shown_at` (no created_at/updated_at — models.md §2.8).
    """

    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="displays",
    )
    # Shown to which contact (null = anonymous). NOT an FK (ADR-008).
    contact_id = models.UUIDField(null=True, blank=True)
    response = models.ForeignKey(
        Response,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="display",
    )
    shown_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "responses_display"
        indexes = [
            models.Index(fields=["survey"], name="ix_display_survey"),
            models.Index(fields=["survey", "shown_at"], name="ix_display_survey_shown"),
            models.Index(fields=["contact_id"], name="ix_display_contact"),
        ]

    def __str__(self) -> str:
        return f"display {self.id} of survey {self.survey_id}"


class Tag(TimeStampedUUIDModel):
    """Label for responses (models.md §2.9)."""

    name = models.CharField(max_length=128)
    color = models.CharField(max_length=16, blank=True)
    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True)

    objects = UserHydrationManager()

    class Meta:
        db_table = "responses_tag"
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uniq_tag_name"),
        ]

    def __str__(self) -> str:
        return self.name


class ResponseTag(UUIDPrimaryKeyMixin):
    """M2M through-table linking responses ↔ tags (models.md §2.9).

    ONLY `created_at` (no updated_at — models.md §2.9).
    """

    response = models.ForeignKey(
        Response,
        on_delete=models.CASCADE,
        related_name="response_tags",
    )
    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name="response_tags",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "responses_responsetag"
        constraints = [
            models.UniqueConstraint(fields=["response", "tag"], name="uniq_response_tag"),
        ]
        indexes = [
            models.Index(fields=["tag"], name="ix_responsetag_tag"),
            models.Index(fields=["response"], name="ix_responsetag_response"),
        ]

    def __str__(self) -> str:
        return f"{self.response_id} tagged {self.tag_id}"
