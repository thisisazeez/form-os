from django.apps import AppConfig


class SegmentsConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `Segment` (contracts/models.md §2.13)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.segments"
    label = "segments"
