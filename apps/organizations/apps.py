from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    """PUBLIC-schema app (SHARED_APPS). Owns the TENANT_MODEL (`Organization`),
    the TENANT_DOMAIN_MODEL (`Domain`), plus `Membership` and `Invitation`.
    See contracts/models.md §1.2–§1.5."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organizations"
    label = "organizations"
