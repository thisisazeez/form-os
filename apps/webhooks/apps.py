from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `Webhook` (contracts/models.md §2.14;
    Phase 7). NOTE: models.md PART B groups Webhook + ApiKey under an `integrations` app;
    the Phase-0 brief split them into `webhooks` + `apikeys`. See models.py for the
    placement note the Wave-2 model owner must resolve via re-broadcast if consolidating."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.webhooks"
    label = "webhooks"
