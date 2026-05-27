from django.apps import AppConfig


class EnvironmentsConfig(AppConfig):
    """TENANT-schema placeholder package. The `Environment` model lives in the
    `workspaces` app per contracts/models.md PART B (§2.2). This package exists because
    the Phase-0 brief enumerated `environments` as a tenant app; it carries no models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.environments"
    label = "environments"
