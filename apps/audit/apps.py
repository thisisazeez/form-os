from django.apps import AppConfig


class AuditConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `AuditLog` (contracts/models.md §2.16;
    Phase 7 — model frozen now)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    label = "audit"
