from django.apps import AppConfig


class TeamsConfig(AppConfig):
    """TENANT-schema app (TENANT_APPS). Wave 2 owns `Team`, `TeamMembership`,
    `WorkspaceTeam` (contracts/models.md §2.3–§2.5). This is the RBAC core
    (contracts/rbac.md)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.teams"
    label = "teams"
