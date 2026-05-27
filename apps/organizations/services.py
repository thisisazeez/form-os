"""
Organization services — org creation, invitations, and membership management.

Single home for the org-level business logic (public schema). DRF + HTMX views both call
these; the last-owner invariant and the reserved-slug rule are enforced here so no surface
can bypass them.

Domain errors raise typed exceptions that the view layer maps to HTTP codes:
  - ``ReservedSlugError`` / ``SlugTakenError`` / generic ``ValidationError`` → 400
  - ``DuplicatePendingInvitation``                                          → 409
  - ``LastOwnerError``                                                      → 409
  - ``InvitationError``                                                     → 400

Flows (mapped to openapi.yaml):
  - ``create_organization``  → POST /api/v1/organizations   (see SUMMARY: not in openapi;
                               the only place an org can be made before a slug exists)
  - ``invite_member``        → POST /api/v1/{org_slug}/invitations
  - ``revoke_invitation``    → DELETE /api/v1/{org_slug}/invitations/{invitation_id}
  - ``accept_invitation``    → POST /api/v1/invitations/accept
  - ``change_member_role``   → PATCH /api/v1/{org_slug}/members/{membership_id}
  - ``remove_member``        → DELETE /api/v1/{org_slug}/members/{membership_id}

Cross-schema note: org creation provisions the tenant Postgres schema (via
``Organization.save()`` → ``TenantMixin``). The owner ``Membership`` + ``Domain`` are
public→public rows. We do NOT touch tenant apps (workspaces/teams) here — Wave 2b owns the
default-team write described in models.md §1.5; this producer is org-level only.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from .enums import InviteStatus, MembershipStatus, OrgRole
from .models import Domain, Invitation, Membership, Organization, validate_org_slug

User = get_user_model()


def first_active_org(user):
    """The user's first (oldest) org with an ACTIVE membership, or ``None``.

    Public-schema read (``Membership`` is a SHARED_APPS model — ADR-002), so it is correct
    regardless of the active schema. Drives the post-login / landing destination: a user with
    a membership goes to that org's dashboard, a user with none goes to the create-org page.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    with schema_context(get_public_schema_name()):
        membership = (
            Membership.objects.filter(user=user, status=MembershipStatus.ACTIVE)
            .select_related("organization")
            .order_by("created_at")
            .first()
        )
        return membership.organization if membership else None


# --- typed errors -----------------------------------------------------------

class ReservedSlugError(ValidationError):
    """Slug is in RESERVED_SLUGS (→ 400)."""


class SlugTakenError(ValidationError):
    """Slug / schema_name already in use (→ 400)."""


class DuplicatePendingInvitation(ValidationError):
    """A pending invitation for this email already exists in the org (→ 409)."""


class InvitationError(ValidationError):
    """Invalid / expired / already-accepted token, or email mismatch (→ 400)."""


class LastOwnerError(ValidationError):
    """The action would remove or demote the org's last owner (→ 409)."""


class OwnershipGrantError(ValidationError):
    """Only an owner may grant ownership (→ 409 per openapi members PATCH)."""


# --- helpers ----------------------------------------------------------------

def active_owner_count(organization, *, exclude_membership_id=None) -> int:
    """Number of active owners in the org, optionally excluding one membership.

    Used by the last-owner invariant: the *excluded* membership is the one being changed,
    so we ask "would any other active owner remain?".

    NOTE: this is an unlocked read (plain ``.count()``). It is safe for read-only callers
    (``can()`` / serializers doing a *display*-time check), but it is NOT sufficient to
    enforce the last-owner invariant against concurrent mutations: two requests each
    demoting/removing a *different* owner can both observe "1 other owner remains" and both
    proceed → 0 owners (a TOCTOU race). The owner-affecting mutations
    (``change_member_role`` / ``remove_member``) therefore re-count under a
    ``select_for_update()`` row lock inside the same transaction — see
    ``_locked_other_active_owner_count`` below — and do NOT rely on this helper for the
    decisive check.
    """
    qs = Membership.objects.filter(
        organization=organization,
        role=OrgRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    if exclude_membership_id is not None:
        qs = qs.exclude(pk=exclude_membership_id)
    return qs.count()


def _locked_other_active_owner_count(organization, *, exclude_membership_id) -> int:
    """Count active owners (excluding one membership) while holding a row lock on them.

    MUST be called inside an open ``transaction.atomic()`` block (``select_for_update``
    requires one). Locks the *full* set of active-owner rows for the org — including the
    membership being changed — so a concurrent owner-affecting mutation blocks until this
    transaction commits/rolls back. The lock set is the same predicate
    ``active_owner_count`` reads, closing the TOCTOU window between "count owners" and
    "save/delete".

    Returns the number of *other* active owners (the target row is subtracted from the
    locked set), i.e. "would any other active owner remain after this mutation?". A return
    of ``0`` means the target is the last owner and the caller must refuse.

    ``order_by("pk")`` gives a deterministic row-lock acquisition order so two concurrent
    transactions locking this set can never deadlock against each other.
    """
    locked_owners = list(
        Membership.objects.select_for_update()
        .filter(
            organization=organization,
            role=OrgRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        .order_by("pk")
    )
    return sum(1 for m in locked_owners if m.pk != exclude_membership_id)


def _slugify_schema_name(slug: str) -> str:
    """Derive a Postgres-safe schema_name from the slug.

    models.md §1.2: schema_name derives from slug. Slugs are already URL-safe
    (``SlugField``: ``[-a-zA-Z0-9_]``); Postgres identifiers can't contain '-', so we map
    hyphens to underscores. Capped at 63 (the Postgres identifier limit, also the field's
    max_length).
    """
    return slug.replace("-", "_")[:63]


# --- org creation -----------------------------------------------------------

def create_organization(*, name: str, slug: str, owner) -> Organization:
    """Create an org (+ tenant schema), a shared Domain, and the owner Membership.

    Steps:
      1. ``Organization.save()`` runs ``full_clean()`` (reserved-slug + field validation)
         then provisions the Postgres schema + runs tenant migrations (django-tenants).
      2. A shared ``Domain`` row (``is_primary=True``) points the org at the base domain
         (ADR-001 path routing — the slug in the URL does the actual tenant selection).
      3. The creating user gets an ``owner`` ``Membership`` (so every org has ≥1 owner —
         the invariant in rbac.md §2).

    Reserved slug → :class:`ReservedSlugError`; duplicate slug/schema → :class:`SlugTakenError`.

    Transaction note: the schema CREATE is DDL and commits outside the ORM transaction. We
    wrap the Domain + Membership writes in ``atomic()``; if those fail we roll back the org
    too (``force_drop`` the just-created schema) so a half-provisioned org isn't left behind.
    """
    name = (name or "").strip()
    slug = (slug or "").strip().lower()
    if not name:
        raise ValidationError({"name": "Name is required."})
    if not slug:
        raise ValidationError({"slug": "Slug is required."})

    # Surface the reserved-slug rule as a typed 400 BEFORE attempting any schema work
    # (the model also enforces this in save()/full_clean(); we pre-check for a clean error).
    try:
        validate_org_slug(slug)
    except ValidationError as exc:
        raise ReservedSlugError({"slug": exc.messages[0]}) from exc

    schema_name = _slugify_schema_name(slug)

    org = Organization(name=name, slug=slug, schema_name=schema_name)
    try:
        # save() → full_clean() (reserved-slug + validators + uniqueness) → TenantMixin
        # schema provision. full_clean() runs the uniqueness check, so a duplicate slug
        # surfaces here as a ValidationError (NOT an IntegrityError) — we classify both.
        org.save()
    except ValidationError as exc:
        message_dict = getattr(exc, "message_dict", {})
        slug_msgs = [str(m) for m in message_dict.get("slug", [])]
        schema_msgs = [str(m) for m in message_dict.get("schema_name", [])]
        # Reserved-slug → 400 (typed).
        if any("reserved" in m.lower() for m in slug_msgs):
            raise ReservedSlugError({"slug": slug_msgs[0]}) from exc
        # Uniqueness collision on slug or derived schema_name → 400 (typed) "slug taken".
        if any("already exists" in m.lower() for m in slug_msgs + schema_msgs):
            raise SlugTakenError(
                {"slug": "An organization with this slug already exists."}
            ) from exc
        raise
    except IntegrityError as exc:
        # Race past the full_clean() uniqueness check → DB constraint wins.
        raise SlugTakenError(
            {"slug": "An organization with this slug already exists."}
        ) from exc

    try:
        with transaction.atomic():
            # Domain is vestigial under path-based routing — TenantSlugMiddleware resolves the
            # tenant from Organization.slug, never from the Domain table (ADR-001). But
            # django-tenants wants a row AND Domain.domain is GLOBALLY UNIQUE, so it must be
            # unique per org: a shared BASE_DOMAIN ("localhost") makes the 2nd org's insert
            # collide (duplicate key → 500), which silently blocked multi-org. Prefix the slug
            # so every org gets a distinct, stable domain string.
            base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
            Domain.objects.create(
                tenant=org,
                domain=f"{org.slug}.{base_domain}",
                is_primary=True,
            )
            Membership.objects.create(
                user=owner,
                organization=org,
                role=OrgRole.OWNER,
                status=MembershipStatus.ACTIVE,
            )
        # Give the new org a default workspace (+ production/development environments) so it is
        # immediately usable: surveys live under a workspace's environments, so an org with none
        # is a dead end. Created in the org's TENANT schema (workspaces are tenant-scoped). Local
        # imports keep this org-level service free of a module-level dependency on the workspaces
        # app. If it fails, the except below force-drops the half-created org.
        from django_tenants.utils import tenant_context

        from apps.workspaces.services import create_workspace as _create_default_workspace

        with tenant_context(org):
            _create_default_workspace(
                name="Default workspace", slug=None, created_by_user_id=owner.id
            )
    except Exception:
        # Roll back the provisioned schema so we don't strand a half-created org.
        org.delete(force_drop=True)
        raise

    return org


# --- invitations ------------------------------------------------------------

def invite_member(*, organization, email: str, role: str, invited_by) -> Invitation:
    """Create (or supersede) a pending invitation and email it.

    - Re-inviting an email that already has a pending invite supersedes the old one:
      the prior pending row is marked ``revoked`` (freeing the partial unique constraint),
      then a fresh invite is created. This matches models.md §1.5 ("re-inviting supersedes")
      while honoring ``uniq_pending_invite_per_email_org``.
    - If the email is already an active member, raise a 409-style duplicate.
    """
    email = User.objects.normalize_email((email or "").strip()).lower()
    if not email:
        raise ValidationError({"email": "Email is required."})
    if role not in OrgRole.values:
        raise ValidationError({"role": f"Invalid role '{role}'."})

    # Already a member of this org?
    if Membership.objects.filter(
        organization=organization, user__email__iexact=email
    ).exists():
        raise DuplicatePendingInvitation(
            {"email": "This user is already a member of the organization."}
        )

    with transaction.atomic():
        # Supersede any existing pending invite for this (org, email).
        Invitation.objects.filter(
            organization=organization,
            email__iexact=email,
            status=InviteStatus.PENDING,
        ).update(status=InviteStatus.REVOKED, updated_at=timezone.now())

        invitation = Invitation.objects.create(
            organization=organization,
            email=email,
            role=role,
            invited_by=invited_by,
            status=InviteStatus.PENDING,
        )

    from apps.accounts import emails as account_emails

    account_emails.send_invitation_email(invitation)
    return invitation


def revoke_invitation(*, organization, invitation_id) -> None:
    """Revoke a pending invitation. No-op-safe error if it's not pending."""
    try:
        invitation = Invitation.objects.get(
            pk=invitation_id, organization=organization
        )
    except Invitation.DoesNotExist as exc:
        raise InvitationError("Invitation not found.") from exc

    if invitation.status != InviteStatus.PENDING:
        raise InvitationError("Only a pending invitation can be revoked.")
    invitation.status = InviteStatus.REVOKED
    invitation.save(update_fields=["status", "updated_at"])


def accept_invitation(*, token: str, user) -> Membership:
    """Accept an invitation by token, creating the caller's Membership.

    The caller MUST be authenticated as the invited email (openapi: "Caller must be logged
    in as the invited email (or signs up first)"). Validates:
      - token resolves to a pending invitation,
      - it hasn't expired (else mark ``expired`` and reject),
      - the logged-in user's email matches the invite email (case-insensitive).

    On success: creates (or reuses) a ``Membership`` with the invited role, marks the
    invitation ``accepted``. Idempotent if the user is already a member (returns the
    existing membership and still marks the invite accepted).
    """
    try:
        invitation = Invitation.objects.select_related("organization").get(token=token or "")
    except Invitation.DoesNotExist as exc:
        raise InvitationError("Invalid or expired invitation token.") from exc

    if invitation.status == InviteStatus.ACCEPTED:
        raise InvitationError("This invitation has already been accepted.")
    if invitation.status != InviteStatus.PENDING:
        raise InvitationError("This invitation is no longer valid.")
    if invitation.expires_at <= timezone.now():
        invitation.status = InviteStatus.EXPIRED
        invitation.save(update_fields=["status", "updated_at"])
        raise InvitationError("This invitation has expired.")

    if (user.email or "").lower() != invitation.email.lower():
        raise InvitationError(
            "This invitation was sent to a different email address."
        )

    with transaction.atomic():
        membership, _created = Membership.objects.get_or_create(
            user=user,
            organization=invitation.organization,
            defaults={"role": invitation.role, "status": MembershipStatus.ACTIVE},
        )
        invitation.status = InviteStatus.ACCEPTED
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["status", "accepted_at", "updated_at"])

    return membership


def accept_pending_invitations(user) -> int:
    """Accept ALL of ``user``'s pending invitations (matched by their email). Returns the count.

    Called on LOGIN so a user who was invited → signed up → verified → logged in is joined to
    the org(s) automatically. Without this, the signup→verify→login chain drops the invite
    token (the verification email carries no invite context), so the invitee lands on the
    create-org page and has to dig the invitation email back up. Best-effort: a single
    expired/used invite is skipped and never blocks login.
    """
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return 0
    with schema_context(get_public_schema_name()):
        tokens = list(
            Invitation.objects.filter(
                email__iexact=email, status=InviteStatus.PENDING
            ).values_list("token", flat=True)
        )
        accepted = 0
        for token in tokens:
            try:
                accept_invitation(token=token, user=user)
                accepted += 1
            except (InvitationError, ValidationError):
                continue  # expired/used/email-mismatch — skip, don't block login
        return accepted


# --- membership management --------------------------------------------------

def change_member_role(*, organization, membership_id, new_role: str, actor_membership) -> Membership:
    """Change a member's org role, enforcing rbac.md §2 invariants.

    Rules:
      - ``new_role`` must be a valid ``OrgRole``.
      - Only an **owner** may grant ownership (admins can't promote to owner) →
        :class:`OwnershipGrantError`.
      - An **admin** cannot change an **owner**'s role (only owners manage owners).
      - Demoting the **last** active owner is refused → :class:`LastOwnerError`.

    ``actor_membership`` is the caller's membership (role already known) so we don't re-query.
    """
    if new_role not in OrgRole.values:
        raise ValidationError({"role": f"Invalid role '{new_role}'."})

    membership = _get_org_membership(organization, membership_id)

    actor_is_owner = actor_membership.role == OrgRole.OWNER

    # Admins cannot act on an owner at all (demote or otherwise).
    if membership.role == OrgRole.OWNER and not actor_is_owner:
        raise OwnershipGrantError(
            {"role": "Only an owner can change another owner's role."}
        )
    # Only owners may grant ownership.
    if new_role == OrgRole.OWNER and not actor_is_owner:
        raise OwnershipGrantError({"role": "Only an owner can grant ownership."})

    # Last-owner invariant (rbac.md §2): demoting an owner is blocked if no other active
    # owner remains. The re-count + save run under a row lock in ONE transaction so two
    # concurrent demotions of *different* owners can't both pass the check and drop the org
    # to zero owners (TOCTOU race — see _locked_other_active_owner_count). Only the
    # owner-demotion branch needs the lock; non-owner role changes keep the lock-free happy
    # path unchanged.
    if membership.role == OrgRole.OWNER and new_role != OrgRole.OWNER:
        with transaction.atomic():
            if _locked_other_active_owner_count(
                organization, exclude_membership_id=membership.pk
            ) == 0:
                raise LastOwnerError(
                    {"role": "Cannot demote the last owner of the organization."}
                )
            membership.role = new_role
            membership.save(update_fields=["role", "updated_at"])
        return membership

    if membership.role != new_role:
        membership.role = new_role
        membership.save(update_fields=["role", "updated_at"])
    return membership


def remove_member(*, organization, membership_id, actor_membership) -> None:
    """Remove a member, enforcing rbac.md §2 invariants.

    Rules:
      - An **admin** cannot remove an **owner** (only owners manage owners).
      - Removing the **last** active owner is refused → :class:`LastOwnerError`.
    """
    membership = _get_org_membership(organization, membership_id)
    actor_is_owner = actor_membership.role == OrgRole.OWNER

    if membership.role == OrgRole.OWNER and not actor_is_owner:
        raise OwnershipGrantError(
            {"detail": "Only an owner can remove another owner."}
        )

    # Last-owner invariant (rbac.md §2): the re-count + delete run under a row lock in ONE
    # transaction so two concurrent removals of *different* owners can't both pass the check
    # and drop the org to zero owners (TOCTOU race — see _locked_other_active_owner_count).
    # Only owner removals need the lock; removing a non-owner keeps the lock-free happy path.
    if membership.role == OrgRole.OWNER:
        with transaction.atomic():
            if _locked_other_active_owner_count(
                organization, exclude_membership_id=membership.pk
            ) == 0:
                raise LastOwnerError(
                    {"detail": "Cannot remove the last owner of the organization."}
                )
            membership.delete()
        return

    membership.delete()


def _get_org_membership(organization, membership_id) -> Membership:
    try:
        return Membership.objects.select_related("user").get(
            pk=membership_id, organization=organization
        )
    except (Membership.DoesNotExist, ValueError, ValidationError) as exc:
        raise ValidationError("Membership not found.") from exc
