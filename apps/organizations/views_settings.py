"""
Server-rendered (HTMX) ORG settings/billing + ACCOUNT settings pages.

A NEW file (the existing apps.organizations.views_htmx is left untouched). These are the
browser counterpart of the org JSON API; reads use the SAME models the API uses.

Pages (TENANT schema, config/urls.py under /<org_slug>/):
  /<org_slug>/settings            GET   — org settings & billing (owner/admin).
  /<org_slug>/settings/billing    GET   — same page, "Billing" sub-nav active.
  /<org_slug>/account             GET/POST — the logged-in user's account (any active member).

RBAC:
  - Org settings/billing is owner/admin only — the org-role gate mirrors members_page
    (can(view_org) is every member, but settings/billing is an admin surface → we check the
    role set directly, returning the same 403 page as the members surface for plain members /
    non-members).
  - Account settings requires only that the caller be an ACTIVE member of the routed org
    (self-service); no can() action — a user always edits THEIR OWN profile.

Cross-schema (ADR-002): the User and Membership both live in the PUBLIC schema, so account
edits + the seat count read under schema_context(public) — exactly like members_page.
"""
from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name, schema_context
from PIL import Image

from apps.contacts.models import Contact
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership
from apps.responses.models import Response as SurveyResponse

User = get_user_model()

_ADMIN_ROLES = (OrgRole.OWNER, OrgRole.ADMIN)

# Avatar upload limits. We NEVER trust the upload's name/content-type — the bytes are opened with
# Pillow to confirm a real image, and the stored file gets a UUID name + whitelist-derived ext.
_AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_AVATAR_FORMATS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}


def _validate_avatar(upload):
    """Validate an uploaded avatar by its actual bytes. Returns (error_message, ext).

    On success: (None, "<ext>") and the upload's read pointer is reset to 0 so it can be saved.
    On failure: ("<human message>", None).
    """
    if upload.size > _AVATAR_MAX_BYTES:
        return ("Image is too large — keep it under 2 MB.", None)
    try:
        img = Image.open(upload)
        fmt = (img.format or "").upper()
        img.verify()  # decodes/validates the bytes; rejects non-images & truncated files
    except Exception:  # noqa: BLE001 — untrusted upload: ANY decode failure (incl. PIL's
        # SyntaxError on malformed chunks, DecompressionBombError, etc.) means "not a valid image".
        return ("That file isn’t a valid image. Use PNG, JPEG, GIF, or WEBP.", None)
    ext = _AVATAR_FORMATS.get(fmt)
    if ext is None:
        return ("Unsupported image type. Use PNG, JPEG, GIF, or WEBP.", None)
    upload.seek(0)  # verify() consumed the stream — reset before the field writes it
    return (None, ext)


# ---------------------------------------------------------------------------
# Org settings & billing
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET"])
def org_settings_page(request, section="general"):
    """``/<org_slug>/settings`` (+ ``/settings/billing``) — org settings & billing (owner/admin).

    A prominent "free, self-hosted — unlimited" banner + usage meters with REAL counts:
      - responses: rows in the routed tenant schema (Response).
      - contacts:  rows in the routed tenant schema (Contact).
      - seats:     ACTIVE memberships of this org (PUBLIC schema).
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    membership = _active_membership(request.user, org)
    if membership is None or membership.role not in _ADMIN_ROLES:
        # Plain member / non-member → same 403 page as the members surface.
        return render(request, "organizations/not_a_member.html", {"org": org}, status=403)

    # Usage counts. Responses + Contacts are tenant-schema (the routed schema is active here).
    responses_count = SurveyResponse.objects.count()
    contacts_count = Contact.objects.count()
    # Seats = active memberships (PUBLIC schema).
    with schema_context(get_public_schema_name()):
        seats_count = Membership.objects.filter(
            organization=org, status=MembershipStatus.ACTIVE
        ).count()

    return render(
        request,
        "settings/org_settings.html",
        {
            "org": org,
            "membership": membership,
            "is_admin": True,
            "section": section,  # "general" | "billing" — drives the sub-nav active tab.
            "responses_count": responses_count,
            "contacts_count": contacts_count,
            "seats_count": seats_count,
        },
    )


# ---------------------------------------------------------------------------
# Account settings (the logged-in user's profile)
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET", "POST"])
def account_settings_page(request):
    """``/<org_slug>/account`` — the logged-in user's profile (any active member).

    Editable: the user's display NAME (wired — written in the PUBLIC schema, ADR-002). Email is
    read-only with a verified chip; timezone is presentational (the User model has no timezone
    column in v1 — adding one is a migration, out of scope). Password + notifications are
    presentational (the password-change flow is the dedicated auth surface; notifications are an
    Alpine UI placeholder until a preferences model exists).
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    # Self-service: only requires the caller be an ACTIVE member of the routed org.
    membership = _active_membership(request.user, org)
    if membership is None:
        return render(request, "organizations/not_a_member.html", {"org": org}, status=403)

    name_saved = False
    avatar_error = None
    if request.method == "POST":
        # Edit the User in the PUBLIC schema (User is public — ADR-002, like members_page).
        op = request.POST.get("op")
        new_name = (request.POST.get("name") or "").strip()
        upload = request.FILES.get("avatar")
        with schema_context(get_public_schema_name()):
            user = User.objects.get(pk=request.user.pk)
            user.name = new_name
            update_fields = ["name", "updated_at"]

            if op == "remove_avatar":
                if user.avatar:
                    user.avatar.delete(save=False)  # also removes the file from storage
                    update_fields.append("avatar")
            elif upload is not None:
                avatar_error, ext = _validate_avatar(upload)
                if avatar_error is None:
                    if user.avatar:
                        user.avatar.delete(save=False)  # don't orphan the previous file
                    # UUID name + whitelist ext — never the user-supplied filename.
                    user.avatar.save(f"{uuid.uuid4().hex}.{ext}", upload, save=False)
                    update_fields.append("avatar")

            user.save(update_fields=update_fields)
            # Refresh the cached request.user so the page re-renders the new name/avatar without a
            # reload (the template reads account=request.user).
            request.user.name = user.name
            request.user.avatar = user.avatar
        name_saved = avatar_error is None

    return render(
        request,
        "settings/account_settings.html",
        {
            "org": org,
            "membership": membership,
            "account": request.user,
            "name_saved": name_saved,
            "avatar_error": avatar_error,
        },
    )


# ---------------------------------------------------------------------------
# helpers (mirror apps.organizations.views_htmx)
# ---------------------------------------------------------------------------
def _routed_org(request):
    org = getattr(request, "tenant", None)
    if org is None or getattr(org, "schema_name", None) == get_public_schema_name():
        return None
    return org


def _active_membership(user, org):
    """The caller's ACTIVE Membership for the routed org (PUBLIC schema), or None."""
    if not user.is_authenticated or org is None:
        return None
    with schema_context(get_public_schema_name()):
        return (
            Membership.objects.select_related("organization")
            .filter(user=user, organization=org, status=MembershipStatus.ACTIVE)
            .first()
        )
