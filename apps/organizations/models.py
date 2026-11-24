"""
Organization / Domain / Membership / Invitation (contracts/models.md §1.2–§1.5).

PUBLIC schema (SHARED_APPS). Public→public FKs are ALLOWED here (User, Organization all
live in the public schema). The cross-schema `user_id` rule (ADR-002) applies only to
TENANT models referencing the public User — it does NOT apply in this module, with one
deliberate exception noted on Invitation.

Tenancy: `Organization(TenantMixin)` → one Postgres schema per org. `Domain(DomainMixin)`
is retained for django-tenants compatibility even though v1 routes by path (ADR-001).
"""
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django_tenants.models import DomainMixin, TenantMixin

from .enums import InviteStatus, MembershipStatus, OrgRole

# Slugs that would collide with reserved top-level paths (models.md §1.2). Rejected at the
# app level (validator), not the DB. `s` is the public survey-runtime prefix (/<slug>/s/...);
# `auth` would shadow the public auth API (/api/v1/auth/...). Keep aligned with
# apps.common.middleware.PUBLIC_CANDIDATES.
RESERVED_SLUGS = frozenset(
    {
        "www", "api", "admin", "app", "static", "s", "public", "media",
        "healthz", "django-admin", "auth", "landing", "favicon.ico",
    }
)


def validate_org_slug(value: str) -> None:
    if value in RESERVED_SLUGS:
        raise ValidationError(f"'{value}' is a reserved slug and cannot be used for an organization.")


def default_invite_expiry():
    """Default invite lifetime: now + 7 days (app-configurable via INVITE_TTL_DAYS)."""
    days = getattr(settings, "INVITE_TTL_DAYS", 7)
    return timezone.now() + timedelta(days=days)


def generate_invite_token() -> str:
    """URL-safe opaque secret for the invite link (models.md §1.5)."""
    return secrets.token_urlsafe(32)


class Organization(TenantMixin):
    """The TENANT model — one Postgres schema per Organization (models.md §1.2).

    TenantMixin provides `schema_name` and (here) `auto_create_schema=True`. Settings:
    TENANT_MODEL = "organizations.Organization".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # schema_name comes from TenantMixin (CharField, unique). It's the Postgres schema id,
    # derived from slug, immutable after create. (Max 63 = Postgres identifier limit.)

    name = models.CharField(max_length=255)

    # URL-safe org identifier — the routing key for ADR-001 path-based routing
    # (/<org_slug>/...). Immutable after create (changing it breaks public links).
    slug = models.SlugField(max_length=63, unique=True, validators=[validate_org_slug])

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # django-tenants: create the Postgres schema automatically on save.
    auto_create_schema = True

    class Meta:
        db_table = "organizations_organization"

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    def clean(self):
        super().clean()
        # Reserved-slug enforcement (models.md §1.2). Run here so it's part of full_clean().
        validate_org_slug(self.slug)

    def save(self, *args, **kwargs):
        # Enforce reserved-slug + field validation on EVERY save, not just forms/full_clean()
        # callers. Without this, Organization.objects.create(slug="api") would provision a
        # real schema that shadows the public auth/API paths (reviewer F1). full_clean() must
        # run BEFORE super().save(), because TenantMixin.save() provisions the Postgres schema.
        self.full_clean()
        super().save(*args, **kwargs)


class Domain(DomainMixin):
    """django-tenants domain routing (models.md §1.3).

    Retained for compatibility though v1 routes by path. In path mode a single shared
    Domain row (is_primary=True) points all orgs at the base domain; the custom
    apps.common.middleware.TenantSlugMiddleware resolves the tenant from the slug. Kept so a
    future subdomain switch needs no migration.

    `domain`, `tenant`, `is_primary` all come from DomainMixin; we only override the PK to
    UUID for ADR-003 uniformity. The tenant FK is a same-schema (public→public) FK — allowed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        db_table = "organizations_domain"


class Membership(models.Model):
    """User ↔ Org with an org-level role (models.md §1.4). Public→public FKs — OK."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=16, choices=OrgRole.choices)
    status = models.CharField(
        max_length=16,
        choices=MembershipStatus.choices,
        default=MembershipStatus.ACTIVE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "organizations_membership"
        constraints = [
            # One membership per user per org (models.md §1.4).
            models.UniqueConstraint(
                fields=["user", "organization"], name="uniq_membership_user_org"
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_membership_org"),
            models.Index(fields=["user"], name="ix_membership_user"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.organization_id} ({self.role})"


class Invitation(models.Model):
    """Email-based invite flow (models.md §1.5). Public→public FKs — OK.

    Note (ADR-002 exception): `invited_by` is a FK(User) because this is public→public
    (both rows in the public schema). models.md §1.5 explicitly decides FK here. Tenant
    models get NO such exception.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    # Invitee email — may not yet be a User. Stored lowercased (app-enforced on save).
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=OrgRole.choices)

    # Opaque secret in the invite link (models.md §1.5).
    token = models.CharField(max_length=64, unique=True, default=generate_invite_token)

    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
    )

    status = models.CharField(
        max_length=16, choices=InviteStatus.choices, default=InviteStatus.PENDING
    )
    expires_at = models.DateTimeField(default=default_invite_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "organizations_invitation"
        constraints = [
            # Only ONE pending invite per email per org; re-inviting supersedes (models.md §1.5).
            models.UniqueConstraint(
                "organization",
                Lower("email"),
                condition=models.Q(status=InviteStatus.PENDING),
                name="uniq_pending_invite_per_email_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="ix_invite_org_status"),
        ]

    def __str__(self) -> str:
        return f"invite {self.email} -> {self.organization_id} ({self.status})"

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower()
        super().save(*args, **kwargs)
