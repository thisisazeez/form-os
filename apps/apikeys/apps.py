from django.apps import AppConfig


class ApikeysConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `ApiKey` (contracts/models.md §2.15).
    NOTE: models.md PART B groups ApiKey + Webhook under an `integrations` app; the Phase-0
    brief split them. See models.py for the placement note."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.apikeys"
    label = "apikeys"
