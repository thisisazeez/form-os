"""
DRF API views for organizations / members / invitations
(contracts/openapi.yaml — Organizations tag).

Two URL families (per the middleware + openapi):
  - PUBLIC schema (config/urls_public.py): org creation + accept-invitation. These can't be
    org-prefixed: creation has no slug yet, and acceptance is done by a logged-in invitee
    before they're a member.
  - TENANT-prefixed (config/urls.py, mounted at ``api/v1/...`` after the slug is stripped):
    org get/patch, members list/change-role/remove, invitations list/create/revoke. The
    routed org is ``request.tenant``; org-level RBAC is enforced by the permission classes
    in apps.organizations.permissions (IsOrgMember / IsOrgOwnerOrAdmin) — NOT can() (Wave 2b).

Business logic + invariants (reserved-slug, last-owner, ownership-grant, dup-invite) live in
apps.organizations.services. Typed service errors map to HTTP codes:
  ReservedSlugError/SlugTakenError/ValidationError → 400; DuplicatePendingInvitation,
  LastOwnerError, OwnershipGrantError → 409; InvitationError → 400.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit

from . import services
from .models import Invitation, Membership
from .pagination import OpenapiPageNumberPagination
from .permissions import IsOrgMember, IsOrgOwnerOrAdmin
from .serializers import (
    InvitationAcceptSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    MembershipRoleUpdateSerializer,
    MembershipSerializer,
    OrganizationCreateSerializer,
    OrganizationSerializer,
    OrganizationUpdateSerializer,
)


def _errors(exc: DjangoValidationError) -> dict:
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"detail": exc.messages[0] if exc.messages else "Invalid request."}


# ---------------------------------------------------------------------------
# PUBLIC-schema endpoints (no org slug)
# ---------------------------------------------------------------------------
class OrganizationCreateView(APIView):
    """POST /api/v1/organizations → create org (+ tenant schema + Domain + owner Membership).

    Not in openapi.yaml (the spec only has org-slug-prefixed org endpoints, which presuppose
    an existing org). Documented as an ambiguity in the producer summary. Authenticated.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = OrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            org = services.create_organization(
                name=serializer.validated_data["name"],
                slug=serializer.validated_data["slug"],
                owner=request.user,
            )
        except (services.ReservedSlugError, services.SlugTakenError) as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(OrganizationSerializer(org).data, status=status.HTTP_201_CREATED)


class InvitationAcceptView(APIView):
    """POST /api/v1/invitations/accept → accept by token, creating the caller's Membership.

    Public-schema endpoint (the invitee may not yet be a member of any org). Caller must be
    authenticated as the invited email (openapi). 200 on success; 400 on bad/expired/
    mismatched token.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = InvitationAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            membership = services.accept_invitation(
                token=serializer.validated_data["token"], user=request.user
            )
        except services.InvitationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(MembershipSerializer(membership).data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# TENANT-prefixed endpoints (org slug already stripped by middleware)
# ---------------------------------------------------------------------------
class OrgView(APIView):
    """GET/PATCH /api/v1/{org_slug}/org — view/update org settings.

    GET requires any member (view_org); PATCH requires owner/admin (edit_org). slug immutable.
    """

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAuthenticated(), IsOrgMember(), IsOrgOwnerOrAdmin()]
        return [IsAuthenticated(), IsOrgMember()]

    def get(self, request):
        return Response(OrganizationSerializer(request.organization).data)

    def patch(self, request):
        serializer = OrganizationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = request.organization
        org.name = serializer.validated_data["name"]
        org.save(update_fields=["name", "updated_at"])
        return Response(OrganizationSerializer(org).data)


class MemberListView(APIView):
    """GET /api/v1/{org_slug}/members — list org members (paginated). Any member (view_members)."""

    permission_classes = [IsAuthenticated, IsOrgMember]

    def get(self, request):
        members = (
            Membership.objects.filter(organization=request.organization)
            .select_related("user")
            .order_by("created_at")
        )
        paginator = OpenapiPageNumberPagination()
        page = paginator.paginate_queryset(members, request, view=self)
        data = MembershipSerializer(page, many=True).data
        return paginator.get_paginated_response(data)


class MemberDetailView(APIView):
    """PATCH/DELETE /api/v1/{org_slug}/members/{membership_id} — change role / remove.

    owner/admin only (change_member_role / remove_member). Invariants (last-owner, only
    owners grant/manage ownership) enforced in services → 409.
    """

    permission_classes = [IsAuthenticated, IsOrgMember, IsOrgOwnerOrAdmin]

    def patch(self, request, membership_id):
        serializer = MembershipRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            membership = services.change_member_role(
                organization=request.organization,
                membership_id=membership_id,
                new_role=serializer.validated_data["role"],
                actor_membership=request.org_membership,
            )
        except (services.LastOwnerError, services.OwnershipGrantError) as exc:
            return Response(_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        audit.record_from_request(
            request,
            action=audit.ACTION_MEMBER_ROLE_CHANGED,
            target_type="membership",
            target_id=membership.id,
            metadata={"new_role": membership.role},
        )
        return Response(MembershipSerializer(membership).data, status=status.HTTP_200_OK)

    def delete(self, request, membership_id):
        try:
            services.remove_member(
                organization=request.organization,
                membership_id=membership_id,
                actor_membership=request.org_membership,
            )
        except (services.LastOwnerError, services.OwnershipGrantError) as exc:
            return Response(_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationListCreateView(APIView):
    """GET/POST /api/v1/{org_slug}/invitations — list / create invitations.

    GET: any member (view_members). POST: owner/admin (invite_member). Dup pending → 409.
    """

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrgMember(), IsOrgOwnerOrAdmin()]
        return [IsAuthenticated(), IsOrgMember()]

    def get(self, request):
        invitations = Invitation.objects.filter(
            organization=request.organization
        ).order_by("-created_at")
        return Response(InvitationSerializer(invitations, many=True).data)

    def post(self, request):
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            invitation = services.invite_member(
                organization=request.organization,
                email=serializer.validated_data["email"],
                role=serializer.validated_data["role"],
                invited_by=request.user,
            )
        except services.DuplicatePendingInvitation as exc:
            return Response(_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        audit.record_from_request(
            request,
            action=audit.ACTION_MEMBER_INVITED,
            target_type="invitation",
            target_id=invitation.id,
            metadata={"email": invitation.email, "role": invitation.role},
        )
        return Response(
            InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED
        )


class InvitationDetailView(APIView):
    """DELETE /api/v1/{org_slug}/invitations/{invitation_id} — revoke a pending invitation.

    owner/admin only (revoke_invitation).
    """

    permission_classes = [IsAuthenticated, IsOrgMember, IsOrgOwnerOrAdmin]

    def delete(self, request, invitation_id):
        try:
            services.revoke_invitation(
                organization=request.organization, invitation_id=invitation_id
            )
        except services.InvitationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)
