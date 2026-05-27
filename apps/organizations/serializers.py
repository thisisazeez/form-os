"""
DRF serializers for org settings, members, and invitations
(contracts/openapi.yaml — Organizations tag).

Response shapes mirror the openapi components: ``Organization``, ``Membership``
(embeds ``UserBrief``), ``Invitation`` (NEVER includes the token — only emailed).

Note on users: ``Membership.user`` is a real public→public FK (both rows in the public
schema), so we read it directly — the cross-schema ``hydrate_users()`` helper is only for
TENANT models carrying ``*_user_id`` UUID columns (ADR-002). This is org-level, public-schema.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.accounts.serializers import UserBriefSerializer

from .enums import OrgRole
from .models import Invitation, Membership, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    """openapi ``Organization`` = {id, name, slug, created_at}. slug is immutable."""

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "created_at"]
        read_only_fields = ["id", "slug", "created_at"]


class OrganizationCreateSerializer(serializers.Serializer):
    """Body for POST /api/v1/organizations (org creation — see SUMMARY ambiguity note)."""

    name = serializers.CharField(max_length=255)
    slug = serializers.SlugField(max_length=63)


class OrganizationUpdateSerializer(serializers.Serializer):
    """PATCH /api/v1/{org_slug}/org — only ``name`` is mutable (slug immutable)."""

    name = serializers.CharField(max_length=255)


class MembershipSerializer(serializers.ModelSerializer):
    """openapi ``Membership`` = {id, user: UserBrief, role, status, created_at}."""

    user = UserBriefSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "status", "created_at"]
        read_only_fields = fields


class MembershipRoleUpdateSerializer(serializers.Serializer):
    """PATCH /api/v1/{org_slug}/members/{id} — {role}."""

    role = serializers.ChoiceField(choices=OrgRole.choices)


class InvitationSerializer(serializers.ModelSerializer):
    """openapi ``Invitation`` — token is intentionally excluded (only emailed)."""

    class Meta:
        model = Invitation
        fields = [
            "id",
            "email",
            "role",
            "status",
            "expires_at",
            "accepted_at",
            "created_at",
        ]
        read_only_fields = fields


class InvitationCreateSerializer(serializers.Serializer):
    """POST /api/v1/{org_slug}/invitations — {email, role}."""

    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=OrgRole.choices)


class InvitationAcceptSerializer(serializers.Serializer):
    """POST /api/v1/invitations/accept — {token}."""

    token = serializers.CharField()
