"""
ORG-LEVEL DRF permissions for the auth-orgs producer (Wave 2a, Phase 1).

Scope (per the producer brief): authorization here is **org-level only** — simple
``Membership.role`` checks (owner/admin/member). We deliberately do NOT call the
workspace-level ``can()`` (apps.common.permissions) — that's Wave 2b and its stub still
raises NotImplementedError. These classes therefore implement the rbac.md §2 org matrix
directly and locally.

The routed org comes from ``request.tenant`` (set by TenantSlugMiddleware after it resolves
the org slug from the URL and activates the schema, ADR-001). Org/Membership/Invitation are
PUBLIC-schema models, so these queries run against the public tables regardless of the
active tenant schema — but ``request.tenant`` tells us WHICH org the URL addressed.

``IsOrgMember`` attaches the caller's active ``Membership`` to
``request.org_membership`` so views/role-gates don't re-query.
"""
from __future__ import annotations

from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.permissions import BasePermission

from .enums import MembershipStatus, OrgRole
from .models import Membership


def _routed_org(request):
    """The Organization addressed by the URL (set by TenantSlugMiddleware).

    Returns None if the request didn't resolve a real tenant (public schema / no slug) —
    org-scoped endpoints are only mounted on the tenant urlconf, so in practice this is set,
    but we guard defensively.
    """
    org = getattr(request, "tenant", None)
    if org is None:
        return None
    # The public "tenant" row is not a real org; its schema_name is the public schema name.
    if getattr(org, "schema_name", None) == get_public_schema_name():
        return None
    return org


def _active_membership(user, org):
    """Fetch the user's ACTIVE membership in org (public schema), or None."""
    if user is None or not user.is_authenticated or org is None:
        return None
    with schema_context(get_public_schema_name()):
        return (
            Membership.objects.select_related("organization")
            .filter(user=user, organization=org, status=MembershipStatus.ACTIVE)
            .first()
        )


class IsOrgMember(BasePermission):
    """Caller has an ACTIVE membership in the routed org (else 403).

    Attaches the membership to ``request.org_membership`` for downstream role gates.
    Applied to every org-scoped endpoint (view/edit org, members, invitations).
    """

    message = "You are not a member of this organization."

    def has_permission(self, request, view) -> bool:
        org = _routed_org(request)
        if org is None:
            return False
        membership = _active_membership(request.user, org)
        if membership is None:
            return False
        # Cache for the view + later permission classes.
        request.org_membership = membership
        request.organization = org
        return True


class IsOrgOwnerOrAdmin(BasePermission):
    """Caller's org role is owner OR admin (rbac.md §2: invite/revoke/manage members, edit org).

    Relies on IsOrgMember having run first (it populates ``request.org_membership``); if it
    hasn't, we resolve the membership ourselves so ordering can't silently open access.
    """

    message = "This action requires the owner or admin role."

    def has_permission(self, request, view) -> bool:
        membership = getattr(request, "org_membership", None)
        if membership is None:
            org = _routed_org(request)
            membership = _active_membership(request.user, org)
            if membership is None:
                return False
            request.org_membership = membership
        return membership.role in (OrgRole.OWNER, OrgRole.ADMIN)
