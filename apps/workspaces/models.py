"""
workspaces — TENANT schema (TENANT_APPS). Workspace + Environment (contracts/models.md §2.1–§2.2).

TENANT-schema models: tenant→tenant FKs are fine (same schema); there is NO Organization FK
(the schema itself is the org boundary). Any User reference is a plain indexed UUIDField
(`created_by_user_id`) — NEVER a ForeignKey (cross-schema rule, ADR-002) — resolved via
apps.common.hydration.hydrate_users().

NOTE on app placement (models.md PART B + apps/environments docstring): the `Environment`
model lives HERE in `workspaces`, NOT in the placeholder `environments` app. This honors
the frozen scaffold placement; moving it would be a coordinated re-broadcast (PLAN §15).
"""
from django.core.validators import MinValueValidator
from django.db import models

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel

from .enums import EnvType


class Workspace(TimeStampedUUIDModel):
    """≈ Formbricks "Project" — groups surveys + config (models.md §2.1).

    RBAC access is granted to Workspaces via Teams (WorkspaceTeam, see rbac.md).

    On workspace creation the contract calls for auto-creating two Environment rows
    (production + development, §2.2). That is *behavior*, not schema, and is deferred to the
    Wave 2c service layer — it is intentionally NOT a save()/signal here (keeps models pure;
    avoids hidden writes the reviewer would flag as out-of-scope).
    """

    name = models.CharField(max_length=255)

    # Unique WITHIN the tenant (schema-scoped — each tenant has its own table, so a plain
    # UniqueConstraint is automatically per-tenant). Used in admin URLs
    # /<org_slug>/w/<workspace_slug>/...
    slug = models.SlugField(max_length=255)

    # Project-level "Look & Feel" theme (≈ Formbricks Configuration → Look & Feel). This is
    # the DEFAULT styling for every survey in the workspace; a survey's own ``styling`` (if any)
    # overrides it key-by-key at serve time (apps.workspaces.theme.merge_styling). The shape is
    # EXACTLY the survey Styling object (contracts/survey-schema.json #/$defs/styling:
    # brandColor / questionColor / backgroundColor / buttonColor / buttonTextColor / background /
    # logoUrl / roundness / hideProgressBar / cardArrangement) so the two compose — we do NOT
    # invent a new shape. All keys optional; ``{}`` (default) == no project theme == current
    # behavior unchanged. Validated against #/$defs/styling on write (apps.workspaces.theme).
    survey_theme = models.JSONField(default=dict, blank=True)

    # General config (≈ Formbricks Configuration → General "Recontact Options"). The cooldown
    # period (in DAYS) before the same respondent may be shown ANOTHER survey from this workspace
    # via in-product (website/app) surveys. NULL == no cooldown / off (Formbricks's default), so a
    # respondent is never blocked by recontact. This governs APP-survey re-showing; link surveys
    # (our v1 surface) don't consume it yet — we persist + expose it now for parity (FEATURE_PARITY
    # WS-8 "General (recontact/cooldown)"). 0 == always wait the rest of the day-bucket (still a
    # valid floor); negatives are rejected by the MinValueValidator + the service layer.
    recontact_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(0)],
        help_text="Cooldown in days before re-showing an app survey to the same respondent. "
        "NULL = no cooldown (default).",
    )

    # White-label / remove-branding (Formbricks ENTERPRISE feature; FEATURE_PARITY §Enterprise +
    # §Look & Feel "remove branding [enterprise]"). When True, the PUBLIC runtime HIDES the
    # "Powered by OpenSurvey" footer on the ending/closed screens. Threaded to the runtime via the
    # runtime host config (apps.responses.public_views.public_runtime_host → runtime.html
    # data-remove-branding / runtime_config), NOT via PublicSurvey. Default False == branding shown
    # (current behavior unchanged).
    remove_branding = models.BooleanField(
        default=False,
        help_text="When True, the public survey runtime hides the 'Powered by OpenSurvey' "
        "footer. Default False (branding shown).",
    )

    # Creator (public User PK). NO FK — cross-schema rule (ADR-002). Indexed via the manager
    # pattern; resolve to a User via hydrate_users("created_by_user_id").
    created_by_user_id = models.UUIDField(null=True)

    # `.hydrate_users(...)` ergonomic on the default queryset (models.md PART A).
    objects = UserHydrationManager()

    class Meta:
        db_table = "workspaces_workspace"
        constraints = [
            models.UniqueConstraint(fields=["slug"], name="uniq_workspace_slug"),
        ]
        indexes = [
            models.Index(fields=["slug"], name="ix_workspace_slug"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


class Environment(TimeStampedUUIDModel):
    """prod/dev split (models.md §2.2). API keys, Surveys, Contacts, and Webhooks scope to
    an Environment (not directly to a Workspace)."""

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="environments",
    )
    type = models.CharField(max_length=16, choices=EnvType.choices)

    class Meta:
        db_table = "workspaces_environment"
        constraints = [
            # Exactly one prod + one dev per workspace (models.md §2.2).
            models.UniqueConstraint(
                fields=["workspace", "type"], name="uniq_env_per_workspace_type"
            ),
        ]
        indexes = [
            models.Index(fields=["workspace"], name="ix_environment_workspace"),
        ]

    def __str__(self) -> str:
        return f"{self.workspace_id}/{self.type}"
