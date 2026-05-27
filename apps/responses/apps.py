from django.apps import AppConfig


class ResponsesConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `Response`, `SurveyLink`, `Display`,
    `Tag`, `ResponseTag` (contracts/models.md §2.7–§2.9)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.responses"
    label = "responses"
