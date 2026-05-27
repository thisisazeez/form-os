from django.apps import AppConfig


class FollowupsConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS) — survey follow-up automations (WS-4 parity).

    Owns the per-(follow-up, response) delivery-attempt ledger (``FollowUpDelivery``) and the
    send pipeline (trigger evaluation -> recipient resolution -> recall-resolved subject/body
    render -> enqueue via the existing email infra). The follow-up DEFINITIONS live ON the
    Survey definition (``Survey.follow_ups`` JSON; survey-schema.json #/$defs/followUp), not
    here — this app only persists delivery outcomes + runs the pipeline, mirroring how
    ``apps.webhooks`` owns delivery while subscriptions live on the Webhook row.

    NEW app (not a touch on surveys/responses) per FEATURE_PARITY.md coordination rules:
    "new features prefer NEW apps to avoid migration races." Survey gets ONE tiny column
    (``follow_ups``) owned by the surveys migration; everything else is isolated here.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.followups"
    label = "followups"
