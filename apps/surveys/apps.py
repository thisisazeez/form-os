from django.apps import AppConfig


class SurveysConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `Survey` (contracts/models.md §2.6).
    Survey JSON columns validate against contracts/survey-schema.json (ADR-004)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.surveys"
    label = "surveys"
