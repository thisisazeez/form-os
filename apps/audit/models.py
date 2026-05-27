"""
audit — TENANT schema (TENANT_APPS). Append-only audit log (contracts/models.md §2.16).
Model frozen now; write paths are Phase 7.

TENANT-schema model. `actor_user_id` is the cross-schema User pattern (ADR-002) — NO FK;
null = system/anonymous. Append-only: ONLY `created_at` (no updated_at — models.md §2.16).
"""
from django.db import models

from apps.common.hydration import UserHydrationManager
from apps.common.models import UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin):
    """Append-only audit record (models.md §2.16)."""

    # Who acted (public User PK). NO FK — cross-schema rule (ADR-002). null = system.
    actor_user_id = models.UUIDField(null=True)
    action = models.CharField(max_length=128)  # e.g. survey.published, member.invited
    target_type = models.CharField(max_length=64)  # e.g. survey, membership
    target_id = models.CharField(max_length=64, null=True, blank=True)
    # Before/after diff or context. Callable default (mutable) per Django rules.
    metadata = models.JSONField(default=dict)
    # Actor IP (PII; config-gated, never plaintext-logged — rbac.md §11).
    ip = models.GenericIPAddressField(null=True, blank=True)
    # Append-only: created_at only, NO updated_at (models.md §2.16).
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserHydrationManager()

    class Meta:
        db_table = "audit_auditlog"
        indexes = [
            models.Index(fields=["actor_user_id"], name="ix_auditlog_actor"),
            models.Index(fields=["target_type", "target_id"], name="ix_auditlog_target"),
            models.Index(fields=["created_at"], name="ix_auditlog_created"),
        ]

    def __str__(self) -> str:
        return f"{self.action} on {self.target_type}:{self.target_id}"
