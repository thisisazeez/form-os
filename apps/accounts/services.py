"""
Account services — the auth flows as pure-ish functions (PLAN §9).

This is the single place the auth business logic lives; both the DRF API views and the
HTMX form views call into it, so behavior can't drift between surfaces. Functions raise
``django.core.exceptions.ValidationError`` on domain errors (mapped to HTTP 400/401 at the
view layer) and return model instances on success.

Flows implemented here (mapped to openapi.yaml operationIds / paths):
  - ``signup``                 → POST /api/v1/auth/signup
  - ``verify_email``           → POST /api/v1/auth/verify-email
  - ``authenticate_for_login`` → POST /api/v1/auth/login   (verified-gate enforced here)
  - ``request_password_reset`` → POST /api/v1/auth/password-reset
  - ``confirm_password_reset`` → POST /api/v1/auth/password-reset/confirm

All of these operate on the PUBLIC schema (``User`` is a SHARED_APPS model). They are
schema-agnostic: they never assume a tenant is active.
"""
from __future__ import annotations

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from . import emails, tokens

User = get_user_model()


class EmailAlreadyRegistered(ValidationError):
    """Raised by :func:`signup` when the email is already taken (→ HTTP 409)."""


def _normalize_email(email: str) -> str:
    return User.objects.normalize_email((email or "").strip())


def signup(*, email: str, password: str, name: str = "") -> User:
    """Create an unverified user and email them a verification token.

    - Email is normalized + uniqueness is case-insensitive (model constraint
      ``uniq_user_email_ci``). A clash raises :class:`EmailAlreadyRegistered` (HTTP 409),
      distinct from a generic field-validation 400.
    - The password is run through Django's validators (settings
      ``AUTH_PASSWORD_VALIDATORS``) before hashing.
    - ``is_verified`` stays ``False``; login is blocked until the email is confirmed.
    """
    email = _normalize_email(email)
    if not email:
        raise ValidationError({"email": "Email is required."})

    # Case-insensitive existence check (mirrors the DB constraint) so we can return a clean
    # 409 rather than an opaque IntegrityError. The unique constraint is still the source of
    # truth against a race (handled below).
    if User.objects.filter(email__iexact=email).exists():
        raise EmailAlreadyRegistered({"email": "A user with this email already exists."})

    # Build an unsaved instance so password validators can consider user attributes
    # (UserAttributeSimilarityValidator compares against email/name).
    user = User(email=email, name=(name or "").strip(), is_verified=False)
    validate_password(password, user=user)
    user.set_password(password)

    try:
        with transaction.atomic():
            user.save()
    except IntegrityError as exc:
        # Lost the race on the unique-email constraint.
        raise EmailAlreadyRegistered(
            {"email": "A user with this email already exists."}
        ) from exc

    emails.send_verification_email(user, tokens.make_email_verification_token(user))
    return user


def verify_email(*, token: str) -> User:
    """Confirm a user's email from a signed verification token.

    Idempotent: re-verifying an already-verified user with a still-valid token succeeds.
    Invalid/expired/tampered tokens raise ``ValidationError`` (→ HTTP 400) with a generic
    message (no leak of which failure occurred).
    """
    uid = tokens.read_email_verification_token(token or "")
    if not uid:
        raise ValidationError("Invalid or expired verification token.")
    try:
        user = User.objects.get(pk=uid)
    except (User.DoesNotExist, ValueError, ValidationError) as exc:
        raise ValidationError("Invalid or expired verification token.") from exc

    if not user.is_verified:
        user.is_verified = True
        user.save(update_fields=["is_verified", "updated_at"])
    return user


def authenticate_for_login(*, email: str, password: str) -> User:
    """Authenticate credentials AND enforce the email-verification gate (PLAN §9).

    Returns the ``User`` on success. Raises ``ValidationError`` (→ HTTP 401) on bad
    credentials, an inactive account, OR an unverified email. The error message is uniform
    so an attacker can't distinguish "wrong password" from "exists but unverified".
    """
    email = _normalize_email(email)
    user = authenticate(username=email, password=password)
    if user is None or not user.is_active:
        raise ValidationError("Invalid credentials or unverified email.")
    if not user.is_verified:
        # Login requires a verified email (models.md §1.1 — enforced in the auth path).
        raise ValidationError("Invalid credentials or unverified email.")
    return user


def request_password_reset(*, email: str) -> None:
    """Email a reset link IF the address belongs to an active user — silently otherwise.

    Never reveals whether the account exists (the view always returns 202). An inactive
    user is treated like a non-existent one.
    """
    email = _normalize_email(email)
    if not email:
        return
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user is None:
        return
    emails.send_password_reset_email(user, tokens.make_password_reset_token(user))


def confirm_password_reset(*, token: str, password: str) -> User:
    """Set a new password from a valid reset token (one-shot via password fingerprint).

    Raises ``ValidationError`` (→ HTTP 400) on an invalid/expired/already-used token or a
    password that fails the configured validators.
    """
    def _get_user(uid):
        return User.objects.filter(pk=uid).first()

    user = tokens.read_password_reset_token(token or "", get_user=_get_user)
    if user is None:
        raise ValidationError("Invalid or expired reset token.")

    validate_password(password, user=user)
    user.set_password(password)
    user.save(update_fields=["password", "updated_at"])
    return user
