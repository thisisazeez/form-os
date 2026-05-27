"""
Server-rendered (HTMX) org pages — the browser counterpart of the org JSON API.

All business logic is delegated to apps.organizations.services (the same layer the DRF API
uses). Org-level RBAC for the tenant-scoped pages reuses the same Membership-role checks as
the API permission classes (NOT can(), which is Wave 2b).

Pages:
  PUBLIC schema (config/urls_public.py, under /auth/ per the middleware deviation):
    /auth/invitations/accept   GET ?token=… (show + accept if logged-in) / POST accept
    /auth/organizations/new    GET form / POST create org → redirect to the org dashboard
  TENANT schema (config/urls.py, under /<org_slug>/):
    /<org_slug>/members        GET list members + invitations + invite form (owner/admin)
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name, schema_context

from apps.audit import services as audit
from apps.common.permissions import can

from . import services
from .enums import InviteStatus, MembershipStatus, OrgRole
from .forms import InvitationCreateForm, OrganizationCreateForm
from .models import Invitation, Membership


# ---------------------------------------------------------------------------
# PUBLIC pages
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def accept_invitation_page(request):
    """``/auth/invitations/accept?token=…`` — show the invite, accept it when logged in.

    - Anonymous visitor → render the page with a prompt to log in or sign up (the token is
      preserved in the link so they return here after authenticating).
    - Authenticated visitor → POST accepts via the service; on success redirect to the org.
    """
    token = request.GET.get("token") or request.POST.get("token", "")
    invitation = _lookup_pending_invitation(token)

    ctx = {"token": token, "invitation": invitation}
    if invitation is None:
        ctx["error"] = "This invitation link is invalid, expired, or already used."
        return render(request, "organizations/accept_invitation.html", ctx)

    if not request.user.is_authenticated:
        ctx["needs_auth"] = True
        return render(request, "organizations/accept_invitation.html", ctx)

    if request.method == "POST":
        try:
            membership = services.accept_invitation(token=token, user=request.user)
        except services.InvitationError as exc:
            ctx["error"] = exc.messages[0] if exc.messages else "Could not accept invitation."
            return render(request, "organizations/accept_invitation.html", ctx)
        # Land on the org dashboard (tenant home) under the org slug.
        return redirect(f"/{membership.organization.slug}/")

    return render(request, "organizations/accept_invitation.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def create_organization_page(request):
    """``/auth/organizations/new`` — authenticated user creates an org (+ schema + owner)."""
    if request.method == "POST":
        form = OrganizationCreateForm(request.POST)
        if form.is_valid():
            try:
                org = services.create_organization(
                    name=form.cleaned_data["name"],
                    slug=form.cleaned_data["slug"],
                    owner=request.user,
                )
            except (services.ReservedSlugError, services.SlugTakenError) as exc:
                _attach_errors(form, exc, default_field="slug")
            except ValidationError as exc:
                _attach_errors(form, exc, default_field="name")
            else:
                return redirect(f"/{org.slug}/")
    else:
        form = OrganizationCreateForm()
    return render(request, "organizations/create_organization.html", {"form": form})


# ---------------------------------------------------------------------------
# TENANT pages
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET", "POST"])
def members_page(request):
    """``/<org_slug>/members`` — list members + invitations; owner/admin can invite.

    The org is the routed tenant (``request.tenant``, set by TenantSlugMiddleware). We
    resolve the caller's membership the same way the API permission layer does.
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    membership = _active_membership(request.user, org)
    if membership is None:
        # Not a member of this org → 403-equivalent page.
        return render(request, "organizations/not_a_member.html", {"org": org}, status=403)

    is_admin = membership.role in (OrgRole.OWNER, OrgRole.ADMIN)
    actor_is_owner = membership.role == OrgRole.OWNER
    invite_form = InvitationCreateForm()
    invite_error = None
    role_error = None

    if request.method == "POST":
        if not is_admin:
            return render(
                request, "organizations/not_a_member.html", {"org": org}, status=403
            )
        # Two POST surfaces on this page: a role change (op=change_role) and the invite form.
        if request.POST.get("op") == "change_role":
            try:
                changed = services.change_member_role(
                    organization=org,
                    membership_id=request.POST.get("membership_id"),
                    new_role=request.POST.get("role", ""),
                    actor_membership=membership,
                )
            except ValidationError as exc:
                # Covers not-found, last-owner, owner-only-grant, admin-can't-touch-owner.
                role_error = exc.messages[0] if exc.messages else "Could not change role."
            else:
                audit.record_from_request(
                    request,
                    action=audit.ACTION_MEMBER_ROLE_CHANGED,
                    target_type="membership",
                    target_id=changed.id,
                    metadata={"new_role": changed.role, "via": "htmx"},
                )
                return redirect(f"/{org.slug}/members")
        else:
            invite_form = InvitationCreateForm(request.POST)
            if invite_form.is_valid():
                try:
                    invitation = services.invite_member(
                        organization=org,
                        email=invite_form.cleaned_data["email"],
                        role=invite_form.cleaned_data["role"],
                        invited_by=request.user,
                    )
                except services.DuplicatePendingInvitation as exc:
                    invite_error = exc.messages[0] if exc.messages else "Already invited."
                except ValidationError as exc:
                    invite_error = exc.messages[0] if exc.messages else "Invalid invitation."
                else:
                    audit.record_from_request(
                        request,
                        action=audit.ACTION_MEMBER_INVITED,
                        target_type="invitation",
                        target_id=invitation.id,
                        metadata={"email": invitation.email, "role": invitation.role, "via": "htmx"},
                    )
                    # reverse() drops the org slug (stripped by TenantSlugMiddleware) → /members
                    # would 404; build the slug-prefixed URL so the post-invite redirect works.
                    return redirect(f"/{org.slug}/members")

    members = list(
        Membership.objects.filter(organization=org).select_related("user").order_by("created_at")
    )
    # Per-member: can the caller change THIS member's role? (owner/admin, minus the invariants
    # — admins can't touch owners, last owner can't be demoted). Drives the inline role <select>
    # vs a static pill. Delegates to the single authorization entry point.
    for m in members:
        m.can_change_role = can(request.user, "change_member_role", m)
    invitations = list(
        Invitation.objects.filter(
            organization=org, status=InviteStatus.PENDING
        ).order_by("-created_at")
    )

    return render(
        request,
        "organizations/members.html",
        {
            "org": org,
            "membership": membership,
            "is_admin": is_admin,
            "actor_is_owner": actor_is_owner,
            "members": members,
            "invitations": invitations,
            "invite_form": invite_form,
            "invite_error": invite_error,
            "role_error": role_error,
        },
    )


# ---------------------------------------------------------------------------
# helpers (mirror apps.organizations.permissions, kept local to the HTMX surface)
# ---------------------------------------------------------------------------
def _routed_org(request):
    org = getattr(request, "tenant", None)
    if org is None or getattr(org, "schema_name", None) == get_public_schema_name():
        return None
    return org


def _active_membership(user, org):
    if not user.is_authenticated or org is None:
        return None
    with schema_context(get_public_schema_name()):
        return (
            Membership.objects.select_related("organization")
            .filter(user=user, organization=org, status=MembershipStatus.ACTIVE)
            .first()
        )


def _lookup_pending_invitation(token: str):
    if not token:
        return None
    with schema_context(get_public_schema_name()):
        inv = (
            Invitation.objects.select_related("organization")
            .filter(token=token, status=InviteStatus.PENDING)
            .first()
        )
    if inv is None:
        return None
    if inv.expires_at <= timezone.now():
        return None
    return inv


def _attach_errors(form, exc: ValidationError, *, default_field: str) -> None:
    if hasattr(exc, "message_dict"):
        for field, messages in exc.message_dict.items():
            target = field if field in form.fields else None
            for msg in messages:
                form.add_error(target, msg)
    else:
        target = default_field if default_field in form.fields else None
        for msg in exc.messages:
            form.add_error(target, msg)
