"""
`User` — custom, email-login user (contracts/models.md §1.1).

PUBLIC schema (SHARED_APPS). Replaces Django's default user:
`AUTH_USER_MODEL = "accounts.User"`. Extends AbstractBaseUser + PermissionsMixin,
NO username, email is the identifier, UUID PK (ADR-003).

A user may belong to many orgs — that lives in `organizations.Membership` (public),
never on this model. Per-team access lives in tenant schemas via `user_id` columns
(the cross-schema pattern, ADR-002).
"""
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Login identifier. Case-insensitive uniqueness enforced via the constraint below.
    email = models.EmailField(unique=True)

    name = models.CharField(max_length=255, blank=True)

    # Optional profile photo. Stored under MEDIA_ROOT/avatars/<uuid>.<ext>; the view assigns a
    # UUID filename and validates the bytes are a real image (never trusts the upload name/type).
    # Empty → the UI falls back to the user's initial. ImageField requires Pillow.
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)

    # Email verification gate (PLAN §9). Login allowed only when True — enforced in the
    # auth backend (Wave 2), NOT at the DB level.
    is_verified = models.BooleanField(default=False)

    # Django standard flags.
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(
        default=False,
        help_text="Django-admin access only (superuser tooling). NOT an org role.",
    )
    # is_superuser comes from PermissionsMixin; it bypasses org RBAC ONLY in the Django
    # admin site, never for the tenant API/HTMX surfaces (rbac.md §2 — can() ignores it).

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # email + password are the only required fields.

    class Meta:
        db_table = "accounts_user"
        constraints = [
            # Case-insensitive email uniqueness (models.md §1.1 constraints).
            models.UniqueConstraint(Lower("email"), name="uniq_user_email_ci"),
        ]

    def __str__(self) -> str:
        return self.email
