from django.apps import AppConfig


class CommonConfig(AppConfig):
    """Shared library app — no models. Holds the cross-schema hydration helper, base
    model mixins (UUID PK + timestamps), the RBAC `can()` entry point (Wave 2), and
    project-wide views (landing/health). See contracts/models.md PART A & PART B
    (`common` = shared lib, no models)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"
