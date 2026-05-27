from django.apps import AppConfig


class ActionsConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Owns ``ActionClass`` — the user-action definitions
    that TRIGGER website/app (in-product) surveys (FEATURE_PARITY WS-11 "Actions / Events").

    An ActionClass is a per-Environment, named user-action ("code" key, "pageview" url-match,
    or "click" CSS-selector). App-type surveys reference ActionClass ids in
    ``Survey.triggers``; the public widget ``track`` endpoint matches a tracked action against
    these to decide which surveys should display. Mirrors Formbricks's ActionClass.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.actions"
    label = "actions"
