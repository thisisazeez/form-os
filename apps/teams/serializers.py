"""
DRF serializers for Teams / TeamMembership / WorkspaceTeam (contracts/openapi.yaml — Teams).

Response shapes mirror the openapi components:
  - ``Team``          = {id, name, member_count, created_at}
  - ``TeamMembership`` = {id, user: UserBrief, team_role, created_at}
  - ``TeamDetail``     = Team + {members: [TeamMembership], workspace_access: [WorkspaceTeam]}
  - ``WorkspaceTeam``  = {id, team_id, workspace_id, access}

CROSS-SCHEMA users (ADR-002 / models.md PART A): ``TeamMembership.user_id`` is a plain UUID
column (NO FK to the public User). Serializers MUST read the hydrated ``user_obj`` attribute
(set by ``hydrate_users("user_id")``), NEVER ``User.objects.get`` per row. The member-list
views hydrate the queryset once before serializing.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.accounts.serializers import UserBriefSerializer

from .enums import TeamRole, WorkspaceAccess
from .models import Team, TeamMembership, WorkspaceTeam


class TeamSerializer(serializers.ModelSerializer):
    """openapi ``Team`` = {id, name, member_count, created_at}.

    ``member_count`` reads an annotated ``member_count`` attribute when the view annotates the
    queryset (list path, avoids N+1); otherwise falls back to ``.memberships.count()``.
    """

    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ["id", "name", "member_count", "created_at"]
        read_only_fields = fields

    def get_member_count(self, obj) -> int:
        annotated = getattr(obj, "member_count_annotated", None)
        if annotated is not None:
            return annotated
        return obj.memberships.count()


class TeamMembershipSerializer(serializers.ModelSerializer):
    """openapi ``TeamMembership`` = {id, user: UserBrief, team_role, created_at}.

    ``user`` is sourced from the hydrated ``user_obj`` attribute (cross-schema; set by
    ``hydrate_users("user_id")``). If a ``user_id`` points to a since-deleted public User,
    ``user_obj`` is None and ``user`` serializes as null.
    """

    user = serializers.SerializerMethodField()

    class Meta:
        model = TeamMembership
        fields = ["id", "user", "team_role", "created_at"]
        read_only_fields = fields

    def get_user(self, obj):
        user_obj = getattr(obj, "user_obj", None)
        if user_obj is None:
            return None
        return UserBriefSerializer(user_obj).data


class WorkspaceTeamSerializer(serializers.ModelSerializer):
    """openapi ``WorkspaceTeam`` = {id, team_id, workspace_id, access}."""

    team_id = serializers.UUIDField(read_only=True)
    workspace_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = WorkspaceTeam
        fields = ["id", "team_id", "workspace_id", "access"]
        read_only_fields = fields


class TeamDetailSerializer(serializers.ModelSerializer):
    """openapi ``TeamDetail`` = Team + {members, workspace_access}.

    ``members`` and ``workspace_access`` are read from view-supplied context (already-hydrated
    member list + the team's WorkspaceTeam rows) so no querying happens inside the serializer.
    """

    member_count = serializers.SerializerMethodField()
    members = serializers.SerializerMethodField()
    workspace_access = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ["id", "name", "member_count", "members", "workspace_access", "created_at"]
        read_only_fields = fields

    def get_member_count(self, obj) -> int:
        members = self.context.get("members")
        if members is not None:
            return len(members)
        return obj.memberships.count()

    def get_members(self, obj):
        members = self.context.get("members", [])
        return TeamMembershipSerializer(members, many=True).data

    def get_workspace_access(self, obj):
        links = self.context.get("workspace_access", [])
        return WorkspaceTeamSerializer(links, many=True).data


# --- request bodies ---------------------------------------------------------
class TeamCreateSerializer(serializers.Serializer):
    """POST /api/v1/{org_slug}/teams — {name}."""

    name = serializers.CharField(max_length=255)


class TeamUpdateSerializer(serializers.Serializer):
    """PATCH /api/v1/{org_slug}/teams/{id} — {name}."""

    name = serializers.CharField(max_length=255)


class TeamMemberAddSerializer(serializers.Serializer):
    """POST /api/v1/{org_slug}/teams/{id}/members — {user_id, team_role?}.

    ``user_id`` is the PUBLIC User PK (cross-schema reference stored as a plain column).
    """

    user_id = serializers.UUIDField()
    team_role = serializers.ChoiceField(
        choices=TeamRole.choices, required=False, default=TeamRole.MEMBER
    )


class WorkspaceAccessGrantSerializer(serializers.Serializer):
    """POST /api/v1/{org_slug}/teams/{id}/workspace-access — {workspace_id, access}."""

    workspace_id = serializers.UUIDField()
    access = serializers.ChoiceField(choices=WorkspaceAccess.choices)
