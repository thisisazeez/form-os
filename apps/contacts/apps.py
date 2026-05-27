from django.apps import AppConfig


class ContactsConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `Contact`, `ContactAttributeKey`,
    `ContactAttribute` (contracts/models.md §2.10–§2.12). Primary PII store
    (contracts/rbac.md §11)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.contacts"
    label = "contacts"
