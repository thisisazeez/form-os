from django.apps import AppConfig


class WorkspacesConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `Workspace` and `Environment`
    (contracts/models.md §2.1–§2.2)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workspaces"
    label = "workspaces"
