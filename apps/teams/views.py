"""
DRF API views for Teams / TeamMembership / WorkspaceTeam (contracts/openapi.yaml — Teams).

TENANT-prefixed (mounted at ``api/v1/...`` after the slug is stripped — config/urls.py). The
routed org is ``request.tenant``; ``IsOrgMember`` gates membership and caches
``request.org_membership`` / ``request.organization``.

Authorization delegates to ``apps.common.permissions.can()`` (rbac.md §7). The
``permission_action_map`` on each view declares the action→permission mapping (auditable,
rbac.md §8.1). ``view_team`` and ``manage_team_membership`` are object-scoped special
predicates in can() (team-admin-of-team / membership), so views pass the ``Team`` instance.

Member lists use ``hydrate_users("user_id")`` (ONE public-schema query — ADR-002 / models.md
PART A); the serializers read the hydrated ``user_obj`` attribute, never ``User.objects.get``
per row.

openapi (Teams tag — mapped by path+method):
  GET    /teams                                 → TeamListCreateView.get   (view_team, filtered)
  POST   /teams                                 → TeamListCreateView.post  (create_team)
  GET    /teams/{id}                            → TeamDetailView.get       (view_team → TeamDetail)
  PATCH  /teams/{id}                            → TeamDetailView.patch     (edit_team)
  DELETE /teams/{id}                            → TeamDetailView.delete    (delete_team)
  POST   /teams/{id}/members                    → TeamMemberListView.post  (manage_team_membership)
  DELETE /teams/{id}/members/{user_id}          → TeamMemberDetailView.delete (manage_team_membership)
  POST   /teams/{id}/workspace-access           → TeamWorkspaceAccessView.post (grant_workspace_access)
"""
from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsOrgMember, can

from . import services
from .enums import TeamRole
from .models import Team, TeamMembership
from .serializers import (
    TeamCreateSerializer,
    TeamDetailSerializer,
    TeamMemberAddSerializer,
    TeamMembershipSerializer,
    TeamSerializer,
    TeamUpdateSerializer,
    WorkspaceAccessGrantSerializer,
    WorkspaceTeamSerializer,
)


def _errors(exc: DjangoValidationError) -> dict:
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"detail": exc.messages[0] if exc.messages else "Invalid request."}


def _get_team_or_404(team_id) -> Team:
    try:
        return Team.objects.get(pk=team_id)
    except (Team.DoesNotExist, ValueError, DjangoValidationError) as exc:
        raise Http404("Team not found.") from exc


class TeamListCreateView(APIView):
    """GET (list, filtered by view_team) / POST (create) teams."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_team", "post": "create_team"}

    def get(self, request):
        # view_team filtering (rbac.md §6 + §8.1): owner/admin see all teams; an org member
        # sees only the teams they belong to. Computed without per-row can().
        membership = request.org_membership
        teams_qs = Team.objects.annotate(
            member_count_annotated=Count("memberships")
        ).order_by("name", "id")
        from apps.organizations.enums import OrgRole

        if membership.role not in (OrgRole.OWNER, OrgRole.ADMIN):
            my_team_ids = TeamMembership.objects.filter(
                user_id=request.user.id
            ).values_list("team_id", flat=True)
            teams_qs = teams_qs.filter(id__in=my_team_ids)
        return Response(TeamSerializer(teams_qs, many=True).data)

    def post(self, request):
        if not can(request.user, "create_team", None):
            return Response(
                {"detail": "You do not have permission to create teams."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = TeamCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            team = services.create_team(
                name=serializer.validated_data["name"],
                created_by_user_id=request.user.id,
            )
        except services.TeamNameTakenError as exc:
            return Response(_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(TeamSerializer(team).data, status=status.HTTP_201_CREATED)


class TeamDetailView(APIView):
    """GET (TeamDetail w/ hydrated members) / PATCH (edit) / DELETE a team."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {
        "get": "view_team",
        "patch": "edit_team",
        "delete": "delete_team",
    }

    def get(self, request, team_id):
        team = _get_team_or_404(team_id)
        # view_team: owner/admin always; org member only if in this team (special predicate).
        if not can(request.user, "view_team", team):
            raise Http404("Team not found.")
        # Hydrate members in ONE public-schema query (ADR-002 / models.md PART A).
        members = list(
            TeamMembership.objects.filter(team=team)
            .order_by("created_at")
            .hydrate_users("user_id")
        )
        workspace_access = list(team.workspace_links.select_related("workspace").all())
        data = TeamDetailSerializer(
            team,
            context={"members": members, "workspace_access": workspace_access},
        ).data
        return Response(data)

    def patch(self, request, team_id):
        team = _get_team_or_404(team_id)
        if not can(request.user, "edit_team", team):
            return Response(
                {"detail": "You do not have permission to edit teams."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = TeamUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.update_team(team=team, name=serializer.validated_data["name"])
        except services.TeamNameTakenError as exc:
            return Response(_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(TeamSerializer(team).data)

    def delete(self, request, team_id):
        team = _get_team_or_404(team_id)
        if not can(request.user, "delete_team", team):
            return Response(
                {"detail": "You do not have permission to delete teams."},
                status=status.HTTP_403_FORBIDDEN,
            )
        team.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeamMemberListView(APIView):
    """POST a member to the team (manage_team_membership: org owner/admin OR team-admin)."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"post": "manage_team_membership"}

    def post(self, request, team_id):
        team = _get_team_or_404(team_id)
        # manage_team_membership is an object-scoped special predicate (team-admin of THIS team
        # OR org owner/admin) — pass the team instance.
        if not can(request.user, "manage_team_membership", team):
            return Response(
                {"detail": "You do not have permission to manage this team's membership."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = TeamMemberAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            membership = services.add_team_member(
                organization=request.organization,
                team=team,
                user_id=serializer.validated_data["user_id"],
                team_role=serializer.validated_data.get("team_role", TeamRole.MEMBER),
            )
        except (services.NotAnOrgMemberError, services.AlreadyMemberError) as exc:
            return Response(_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        # Serialize the new membership with its hydrated user (ONE query).
        [hydrated] = TeamMembership.objects.filter(pk=membership.pk).hydrate_users("user_id")
        return Response(
            TeamMembershipSerializer(hydrated).data, status=status.HTTP_201_CREATED
        )


class TeamMemberDetailView(APIView):
    """DELETE a member from the team (manage_team_membership)."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"delete": "manage_team_membership"}

    def delete(self, request, team_id, user_id):
        team = _get_team_or_404(team_id)
        if not can(request.user, "manage_team_membership", team):
            return Response(
                {"detail": "You do not have permission to manage this team's membership."},
                status=status.HTTP_403_FORBIDDEN,
            )
        services.remove_team_member(team=team, user_id=user_id)
        # Idempotent: 204 whether or not a row existed (removing an absent member is a no-op).
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeamWorkspaceAccessView(APIView):
    """POST grant/update this team's access to a workspace (grant_workspace_access)."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"post": "grant_workspace_access"}

    def post(self, request, team_id):
        team = _get_team_or_404(team_id)
        # grant_workspace_access is org owner/admin only (rbac.md §5: shapes org-wide RBAC).
        if not can(request.user, "grant_workspace_access", None):
            return Response(
                {"detail": "You do not have permission to grant workspace access."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = WorkspaceAccessGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            link = services.grant_workspace_access(
                team=team,
                workspace_id=serializer.validated_data["workspace_id"],
                access=serializer.validated_data["access"],
            )
        except services.LastManagePathError as exc:
            return Response(_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(WorkspaceTeamSerializer(link).data, status=status.HTTP_200_OK)
