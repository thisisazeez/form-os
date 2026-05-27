"""
Team services — Team CRUD, membership add/remove, and WorkspaceTeam grants.

Single home for team business logic in the TENANT schema. DRF views call these. Invariants
enforced here (no surface bypasses them):

  - Team names are case-insensitive unique within the tenant (model ``uniq_team_name``;
    surfaced as a typed 409).
  - Adding a member validates the ``user_id`` is an ACTIVE org member (models.md §2.4: "the
    DB cannot enforce [cross-schema integrity]; creation paths validate membership"). This is
    the canonical cross-schema WRITE-path check (mirror of the read-path rule in rbac.md §4).
  - ``WorkspaceTeam`` is ONE row per (team, workspace) (model ``uniq_workspace_team``):
    granting access UPSERTs — updates ``access`` if a row exists, else creates it.

Typed errors map to HTTP codes in the view layer:
  - ``TeamNameTakenError``                         → 409
  - ``NotAnOrgMemberError`` / ``AlreadyMemberError`` → 409
  - generic ``ValidationError``                    → 400
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django_tenants.utils import get_public_schema_name, schema_context

from apps.workspaces.models import Workspace

from .enums import TeamRole
from .models import Team, TeamMembership, WorkspaceTeam


class TeamNameTakenError(ValidationError):
    """A team with this (case-insensitive) name already exists (→ 409)."""


class NotAnOrgMemberError(ValidationError):
    """The user_id is not an active member of this org — cannot add to a team (→ 409)."""


class AlreadyMemberError(ValidationError):
    """The user is already a member of this team (→ 409)."""


class LastManagePathError(ValidationError):
    """Refused: this change would remove the LAST delegated ``manage`` path to a workspace.

    The "last manage path" guard (→ 409). Lowering a team's ``access`` away from ``manage`` —
    or revoking it entirely — is refused when NO OTHER ``WorkspaceTeam`` row grants ``manage``
    on that workspace. (Org owners/admins keep an IMPLICIT ``manage`` on every workspace —
    rbac.md §3 — so they are never locked out; this guard protects the DELEGATED team-grant
    path so a workspace never silently loses every team that can manage it.)
    """


# --- team CRUD --------------------------------------------------------------
def create_team(*, name: str, created_by_user_id) -> Team:
    """Create a team. Duplicate (case-insensitive) name → :class:`TeamNameTakenError`."""
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Name is required."})
    try:
        with transaction.atomic():
            return Team.objects.create(name=name, created_by_user_id=created_by_user_id)
    except IntegrityError as exc:
        raise TeamNameTakenError(
            {"name": f"A team named '{name}' already exists."}
        ) from exc


def update_team(*, team: Team, name: str) -> Team:
    """Rename a team. Duplicate name → :class:`TeamNameTakenError`."""
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Name is required."})
    team.name = name
    try:
        with transaction.atomic():
            team.save(update_fields=["name", "updated_at"])
    except IntegrityError as exc:
        raise TeamNameTakenError(
            {"name": f"A team named '{name}' already exists."}
        ) from exc
    return team


# --- membership -------------------------------------------------------------
def _is_active_org_member(organization, user_id) -> bool:
    """True if ``user_id`` has an ACTIVE ``Membership`` in ``organization`` (public schema).

    Cross-schema WRITE-path validation (models.md §2.4): the tenant has no FK to the public
    User/Membership, so we explicitly check membership before writing a TeamMembership row.
    """
    from apps.organizations.enums import MembershipStatus
    from apps.organizations.models import Membership

    with schema_context(get_public_schema_name()):
        return Membership.objects.filter(
            organization=organization,
            user_id=user_id,
            status=MembershipStatus.ACTIVE,
        ).exists()


def add_team_member(
    *, organization, team: Team, user_id, team_role: str = TeamRole.MEMBER
) -> TeamMembership:
    """Add a user (by public ``user_id``) to a team.

    Validates the user is an ACTIVE member of the routed org (else
    :class:`NotAnOrgMemberError`). A user already in the team → :class:`AlreadyMemberError`.

    ``organization`` is the routed ``request.tenant`` (used for the public-schema membership
    check); the ``TeamMembership`` row itself is written in the active tenant schema.
    """
    if team_role not in TeamRole.values:
        raise ValidationError({"team_role": f"Invalid team_role '{team_role}'."})

    if not _is_active_org_member(organization, user_id):
        raise NotAnOrgMemberError(
            {"user_id": "User is not an active member of this organization."}
        )

    if TeamMembership.objects.filter(team=team, user_id=user_id).exists():
        raise AlreadyMemberError({"user_id": "User is already a member of this team."})

    try:
        with transaction.atomic():
            return TeamMembership.objects.create(
                team=team, user_id=user_id, team_role=team_role
            )
    except IntegrityError as exc:
        # Race past the existence check → uniq_team_membership wins.
        raise AlreadyMemberError(
            {"user_id": "User is already a member of this team."}
        ) from exc


def remove_team_member(*, team: Team, user_id) -> bool:
    """Remove a user from a team. Returns True if a row was deleted, False if not present."""
    deleted, _ = TeamMembership.objects.filter(team=team, user_id=user_id).delete()
    return bool(deleted)


# --- workspace access grants ------------------------------------------------
def _resolve_workspace(workspace_id) -> Workspace:
    """Load the tenant Workspace by id, else a typed 400 (``workspace_id`` not found)."""
    try:
        return Workspace.objects.get(pk=workspace_id)
    except (Workspace.DoesNotExist, ValueError, ValidationError) as exc:
        raise ValidationError({"workspace_id": "Workspace not found."}) from exc


def _other_manage_team_exists(workspace, *, exclude_team_id) -> bool:
    """True if some team OTHER than ``exclude_team_id`` has ``manage`` on ``workspace``.

    The decisive read for the last-manage-path guard. Org owners/admins are NOT counted here:
    their ``manage`` is implicit (never a ``WorkspaceTeam`` row) and unconditional, so this
    guard concerns only the DELEGATED team-grant path.
    """
    from .enums import WorkspaceAccess

    return (
        WorkspaceTeam.objects.filter(workspace=workspace, access=WorkspaceAccess.MANAGE)
        .exclude(team_id=exclude_team_id)
        .exists()
    )


def grant_workspace_access(*, team: Team, workspace_id, access: str) -> WorkspaceTeam:
    """Grant (or update) a team's access to a workspace — UPSERT on (team, workspace).

    One ``WorkspaceTeam`` row per (team, workspace) (model ``uniq_workspace_team``): if a row
    exists, its ``access`` is updated; else a new row is created. The workspace must exist in
    the tenant (else 400).

    LAST-MANAGE-PATH GUARD: if this team currently has ``manage`` and the new ``access`` is a
    LOWER level (``read``/``write``), the change is refused (:class:`LastManagePathError`, 409)
    unless another team still grants ``manage`` on the same workspace.
    """
    from .enums import WorkspaceAccess

    if access not in WorkspaceAccess.values:
        raise ValidationError({"access": f"Invalid access '{access}'."})

    workspace = _resolve_workspace(workspace_id)

    with transaction.atomic():
        existing = (
            WorkspaceTeam.objects.select_for_update()
            .filter(team=team, workspace=workspace)
            .first()
        )
        # Lowering this team away from `manage` → guard the last delegated manage path.
        if (
            existing is not None
            and existing.access == WorkspaceAccess.MANAGE
            and access != WorkspaceAccess.MANAGE
            and not _other_manage_team_exists(workspace, exclude_team_id=team.id)
        ):
            raise LastManagePathError(
                {
                    "access": (
                        "Cannot lower this team's access: it is the only team that can "
                        "manage this workspace. Grant another team manage access first."
                    )
                }
            )
        link, _created = WorkspaceTeam.objects.update_or_create(
            team=team, workspace=workspace, defaults={"access": access}
        )
    return link


def revoke_workspace_access(*, team: Team, workspace_id) -> bool:
    """Revoke a team's access to a workspace — DELETE the ``WorkspaceTeam`` row (set "none").

    Returns True if a row was deleted, False if none existed (idempotent). The workspace must
    exist in the tenant (else 400).

    LAST-MANAGE-PATH GUARD: revoking a team that currently has ``manage`` is refused
    (:class:`LastManagePathError`, 409) unless another team still grants ``manage`` on the same
    workspace.
    """
    from .enums import WorkspaceAccess

    workspace = _resolve_workspace(workspace_id)

    with transaction.atomic():
        existing = (
            WorkspaceTeam.objects.select_for_update()
            .filter(team=team, workspace=workspace)
            .first()
        )
        if existing is None:
            return False
        if existing.access == WorkspaceAccess.MANAGE and not _other_manage_team_exists(
            workspace, exclude_team_id=team.id
        ):
            raise LastManagePathError(
                {
                    "access": (
                        "Cannot revoke this team's access: it is the only team that can "
                        "manage this workspace. Grant another team manage access first."
                    )
                }
            )
        existing.delete()
    return True
