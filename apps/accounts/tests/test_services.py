"""
Service-layer tests for the auth flows (apps.accounts.services).

These exercise the business logic directly (no HTTP), giving fast, schema-light feedback:
signup → verify, login verification-gate, password reset request/confirm, and token
edge-cases (expiry, tampering, single-use). Email assertions use ``mail.outbox`` (locmem
in CI; in dev the console backend won't populate outbox, so those asserts are gated).
"""
import pytest
from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.core.exceptions import ValidationError

from apps.accounts import services, tokens

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_signup_creates_unverified_user_and_sends_email():
    user = services.signup(email="Alice@Example.com", password="sup3rsecretpw", name="Alice")
    assert user.pk is not None
    # Email normalized; account starts unverified.
    assert user.email == "Alice@example.com"  # normalize_email lowercases domain only
    assert user.is_verified is False
    assert user.check_password("sup3rsecretpw")
    # One verification email queued (locmem backend in CI).
    if hasattr(mail, "outbox"):
        assert any("Verify" in m.subject for m in mail.outbox)


def test_signup_duplicate_email_raises_conflict():
    services.signup(email="bob@example.com", password="sup3rsecretpw")
    with pytest.raises(services.EmailAlreadyRegistered):
        services.signup(email="BOB@example.com", password="anotherpw123")


def test_signup_weak_password_rejected():
    with pytest.raises(ValidationError):
        services.signup(email="weak@example.com", password="123")  # too short / numeric


def test_verify_email_sets_is_verified():
    user = services.signup(email="carol@example.com", password="sup3rsecretpw")
    token = tokens.make_email_verification_token(user)
    verified = services.verify_email(token=token)
    verified.refresh_from_db()
    assert verified.is_verified is True


def test_verify_email_bad_token_rejected():
    with pytest.raises(ValidationError):
        services.verify_email(token="not-a-real-token")


def test_verify_email_wrong_salt_rejected():
    """A token signed with a different salt (e.g. a reset token) cannot verify email."""
    user = services.signup(email="dave@example.com", password="sup3rsecretpw")
    reset_token = tokens.make_password_reset_token(user)
    with pytest.raises(ValidationError):
        services.verify_email(token=reset_token)


def test_login_blocked_until_verified():
    services.signup(email="erin@example.com", password="sup3rsecretpw")
    # Unverified → login refused.
    with pytest.raises(ValidationError):
        services.authenticate_for_login(email="erin@example.com", password="sup3rsecretpw")


def test_login_succeeds_after_verification():
    user = services.signup(email="frank@example.com", password="sup3rsecretpw")
    services.verify_email(token=tokens.make_email_verification_token(user))
    logged_in = services.authenticate_for_login(
        email="frank@example.com", password="sup3rsecretpw"
    )
    assert logged_in.pk == user.pk


def test_login_wrong_password_rejected():
    user = services.signup(email="grace@example.com", password="sup3rsecretpw")
    services.verify_email(token=tokens.make_email_verification_token(user))
    with pytest.raises(ValidationError):
        services.authenticate_for_login(email="grace@example.com", password="wrongpw1234")


def test_password_reset_request_no_enumeration_for_unknown_email():
    # Unknown email → silent no-op (no exception, no email).
    if hasattr(mail, "outbox"):
        mail.outbox.clear()
    services.request_password_reset(email="nobody@example.com")
    if hasattr(mail, "outbox"):
        assert mail.outbox == []


def test_password_reset_full_cycle():
    user = services.signup(email="heidi@example.com", password="orig1nalpasswd")
    services.verify_email(token=tokens.make_email_verification_token(user))
    if hasattr(mail, "outbox"):
        mail.outbox.clear()

    services.request_password_reset(email="heidi@example.com")
    if hasattr(mail, "outbox"):
        assert any("Reset" in m.subject for m in mail.outbox)

    token = tokens.make_password_reset_token(User.objects.get(pk=user.pk))
    services.confirm_password_reset(token=token, password="brandNewpw789")

    user.refresh_from_db()
    assert user.check_password("brandNewpw789")
    assert not user.check_password("orig1nalpasswd")


def test_password_reset_token_is_single_use():
    """After the password changes, the same reset token can't be replayed (fingerprint)."""
    user = services.signup(email="ivan@example.com", password="orig1nalpasswd")
    services.verify_email(token=tokens.make_email_verification_token(user))
    token = tokens.make_password_reset_token(User.objects.get(pk=user.pk))

    services.confirm_password_reset(token=token, password="firstChange12")
    # Replaying the same token now fails (embedded fingerprint no longer matches).
    with pytest.raises(ValidationError):
        services.confirm_password_reset(token=token, password="secondChange34")


def test_reset_token_expired_rejected(settings):
    user = services.signup(email="judy@example.com", password="orig1nalpasswd")
    token = tokens.make_password_reset_token(user)
    # Force an immediate expiry on read.
    settings.PASSWORD_RESET_TTL_SECONDS = -1
    with pytest.raises(ValidationError):
        services.confirm_password_reset(token=token, password="brandNewpw789")


def test_verify_token_expired_rejected(settings):
    user = services.signup(email="ken@example.com", password="orig1nalpasswd")
    token = tokens.make_email_verification_token(user)
    settings.EMAIL_VERIFY_TTL_SECONDS = -1
    assert tokens.read_email_verification_token(token) is None


def test_reset_token_for_inactive_user_not_sent():
    user = services.signup(email="leo@example.com", password="orig1nalpasswd")
    user.is_active = False
    user.save(update_fields=["is_active"])
    if hasattr(mail, "outbox"):
        mail.outbox.clear()
    services.request_password_reset(email="leo@example.com")
    if hasattr(mail, "outbox"):
        assert mail.outbox == []


def test_token_payload_shapes():
    """Sanity: the signed tokens decode to the expected payload structure."""
    user = services.signup(email="mona@example.com", password="orig1nalpasswd")
    vt = tokens.make_email_verification_token(user)
    decoded = signing.loads(vt, salt=tokens.VERIFY_EMAIL_SALT)
    assert decoded == {"uid": str(user.pk)}
