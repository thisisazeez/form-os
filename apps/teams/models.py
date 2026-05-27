"""
teams — TENANT schema (TENANT_APPS). The RBAC core (contracts/models.md §2.3–§2.5, rbac.md).

TENANT-schema models. tenant→tenant FKs (Team↔Workspace) are fine. The User reference on
TeamMembership is a plain indexed UUIDField (`user_id`) — NEVER a ForeignKey (cross-schema
rule, ADR-002) — resolved via apps.common.hydration.hydrate_users(), never per-row
User.objects.get (REVIEW.md §3.2).
"""
from django.db import models
from django.db.models.functions import Lower

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel
from apps.workspaces.models import Workspace

from .enums import TeamRole, WorkspaceAccess


class Team(TimeStampedUUIDModel):
    """A group of users for access control (models.md §2.3)."""

    name = models.CharField(max_length=255)

    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True)

    objects = UserHydrationManager()

    class Meta:
        db_table = "teams_team"
        constraints = [
            # Team names unique within tenant, case-insensitive (models.md §2.3).
            models.UniqueConstraint(Lower("name"), name="uniq_team_name"),
        ]

    def __str__(self) -> str:
        return self.name


class TeamMembership(TimeStampedUUIDModel):
    """User ↔ Team (models.md §2.4). The canonical cross-schema user read path.

    `user_id` is the PUBLIC User PK as a plain column — NO FK (cross-schema, ADR-002).
    Resolve via hydrate_users("user_id"). The index on `user_id` is CRITICAL: "which teams
    is this user in?" drives every permission check (rbac.md §4).
    """

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    # Public User PK — NO FK (cross-schema, ADR-002). Indexed below.
    user_id = models.UUIDField()
    team_role = models.CharField(
        max_length=16, choices=TeamRole.choices, default=TeamRole.MEMBER
    )

    objects = UserHydrationManager()

    class Meta:
        db_table = "teams_teammembership"
        constraints = [
            # A user is in a team at most once (models.md §2.4).
            models.UniqueConstraint(fields=["team", "user_id"], name="uniq_team_membership"),
        ]
        indexes = [
            # CRITICAL: drives every permission check (models.md §2.4 / rbac.md §4).
            models.Index(fields=["user_id"], name="ix_team_membership_user"),
            models.Index(fields=["team"], name="ix_team_membership_team"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.team_id} ({self.team_role})"


class WorkspaceTeam(TimeStampedUUIDModel):
    """Grants a Team access to a Workspace — the granular RBAC join (models.md §2.5).

    Access level ∈ read / write / manage (ordered read < write < manage). Effective
    permission = max(access) over a user's WorkspaceTeam rows (resolved in
    apps/common/permissions.py, not stored).
    """

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="workspace_links",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="team_links",
    )
    access = models.CharField(max_length=16, choices=WorkspaceAccess.choices)

    class Meta:
        db_table = "teams_workspaceteam"
        constraints = [
            # One access row per (team, workspace); to change access, update `access`.
            models.UniqueConstraint(
                fields=["team", "workspace"], name="uniq_workspace_team"
            ),
        ]
        indexes = [
            models.Index(fields=["workspace"], name="ix_workspace_team_workspace"),
            models.Index(fields=["team"], name="ix_workspace_team_team"),
        ]

    def __str__(self) -> str:
        return f"{self.team_id} -> {self.workspace_id} ({self.access})"
