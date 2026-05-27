"""
segments — TENANT schema (TENANT_APPS). Reusable contact filters (contracts/models.md §2.13).

TENANT-schema model. FK(Environment) is tenant→tenant (OK). `created_by_user_id` is the
cross-schema User pattern (ADR-002). `filter` JSON is validated app-side against
survey-schema.json#/$defs/segmentFilter (builder + analytics share the shape); never
DB-enforced.
"""
from django.db import models
from django.db.models.functions import Lower

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel
from apps.workspaces.models import Environment


class Segment(TimeStampedUUIDModel):
    """A reusable filter definition (models.md §2.13)."""

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="segments",
    )
    title = models.CharField(max_length=255)
    # A boolean tree of attribute/behavior conditions; validated against
    # survey-schema.json#/$defs/segmentFilter. Callable default (mutable) per Django rules.
    filter = models.JSONField(default=dict)
    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True)

    objects = UserHydrationManager()

    class Meta:
        db_table = "segments_segment"
        constraints = [
            models.UniqueConstraint(
                "environment", Lower("title"), name="uniq_segment_title_per_env"
            ),
        ]
        indexes = [
            models.Index(fields=["environment"], name="ix_segment_environment"),
        ]

    def __str__(self) -> str:
        return self.title
