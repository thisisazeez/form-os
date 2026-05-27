from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS).

    Home for the Integrations hub — credential-free integrations that fan out off the EXISTING
    webhook event dispatch (apps.webhooks.services.dispatch_event). The first concrete
    integration is Slack (Incoming Webhook URL — no OAuth). The generic Webhooks app is
    surfaced under this hub by link (not duplicated). Deferred integrations that need
    third-party OAuth/app credentials (Google Sheets, Airtable, Notion, Zapier/Make/n8n) are
    described in the hub but ship NO connect buttons until creds are provided.

    PLACEMENT NOTE: contracts/models.md PART B groups Webhook + ApiKey under an `integrations`
    app; the Phase-0 scaffold split those into `webhooks` + `apikeys`. This `integrations` app
    is NEW and owns only the SlackIntegration model — it does not move Webhook/ApiKey (that
    would be a coordinated re-broadcast). Single migration owner per app (FEATURE_PARITY).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    label = "integrations"
