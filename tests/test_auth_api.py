"""
HTTP-level tests for the auth API (contracts/openapi.yaml — Auth tag).

Exercises the full public-schema flow over DRF's APIClient against the PUBLIC urlconf:
signup → verify-email → login (verified gate) → logout → /me, plus password reset
request/confirm. Email assertions read ``mail.outbox`` (locmem under the test env).

These run on the public schema (no org slug), so plain ``@pytest.mark.django_db`` is fine
— no tenant schema is provisioned here.
"""
import re
from urllib.parse import unquote

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts import tokens

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def _token_from_outbox():
    """Pull a token out of the most recent email body.

    Verify/reset links carry ``?token=<signed token>``. The signed token contains ``:`` /
    base64url chars, which ``urlencode`` percent-encodes — so we capture the non-whitespace
    run after ``token=`` and ``unquote`` it back to the raw token value.
    """
    assert mail.outbox, "expected an email to have been sent"
    body = mail.outbox[-1].body
    m = re.search(r"[?&]token=(\S+)", body)
    assert m, f"no token found in email body:\n{body}"
    return unquote(m.group(1))


# --- signup -----------------------------------------------------------------

def test_signup_returns_201_userbrief_and_sends_email(api):
    mail.outbox.clear()
    resp = api.post(
        "/api/v1/auth/signup",
        {"email": "newuser@example.com", "password": "sup3rsecretpw", "name": "New User"},
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "email", "name"}  # openapi UserBrief
    assert body["email"] == "newuser@example.com"
    assert body["name"] == "New User"
    user = User.objects.get(email="newuser@example.com")
    assert user.is_verified is False
    assert any("Verify" in m.subject for m in mail.outbox)


def test_signup_duplicate_email_returns_409(api):
    api.post(
        "/api/v1/auth/signup",
        {"email": "dup@example.com", "password": "sup3rsecretpw"},
        format="json",
    )
    resp = api.post(
        "/api/v1/auth/signup",
        {"email": "DUP@example.com", "password": "anotherpw1234"},
        format="json",
    )
    assert resp.status_code == 409


def test_signup_short_password_returns_400(api):
    resp = api.post(
        "/api/v1/auth/signup",
        {"email": "x@example.com", "password": "short"},
        format="json",
    )
    # Serializer minLength=8 → DRF 400 before service runs.
    assert resp.status_code == 400


def test_signup_weak_but_long_password_returns_400(api):
    # 8+ chars but purely numeric → passes serializer, fails Django validators in service.
    resp = api.post(
        "/api/v1/auth/signup",
        {"email": "numeric@example.com", "password": "12345678"},
        format="json",
    )
    assert resp.status_code == 400


# --- verify-email -----------------------------------------------------------

def test_verify_email_flow(api):
    mail.outbox.clear()
    api.post(
        "/api/v1/auth/signup",
        {"email": "verifyme@example.com", "password": "sup3rsecretpw"},
        format="json",
    )
    token = _token_from_outbox()
    resp = api.post("/api/v1/auth/verify-email", {"token": token}, format="json")
    assert resp.status_code == 200
    assert User.objects.get(email="verifyme@example.com").is_verified is True


def test_verify_email_bad_token_returns_400(api):
    resp = api.post("/api/v1/auth/verify-email", {"token": "garbage"}, format="json")
    assert resp.status_code == 400


# --- login / verified gate --------------------------------------------------

def test_login_blocked_before_verification_401(api):
    api.post(
        "/api/v1/auth/signup",
        {"email": "unverified@example.com", "password": "sup3rsecretpw"},
        format="json",
    )
    resp = api.post(
        "/api/v1/auth/login",
        {"email": "unverified@example.com", "password": "sup3rsecretpw"},
        format="json",
    )
    assert resp.status_code == 401


def test_login_after_verify_sets_session(api):
    mail.outbox.clear()
    api.post(
        "/api/v1/auth/signup",
        {"email": "loginok@example.com", "password": "sup3rsecretpw"},
        format="json",
    )
    api.post("/api/v1/auth/verify-email", {"token": _token_from_outbox()}, format="json")

    resp = api.post(
        "/api/v1/auth/login",
        {"email": "loginok@example.com", "password": "sup3rsecretpw"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "loginok@example.com"
    # Session cookie set (openapi sessionAuth).
    assert "sessionid" in resp.cookies


def test_login_wrong_password_401(api):
    mail.outbox.clear()
    api.post(
        "/api/v1/auth/signup",
        {"email": "wrongpw@example.com", "password": "sup3rsecretpw"},
        format="json",
    )
    api.post("/api/v1/auth/verify-email", {"token": _token_from_outbox()}, format="json")
    resp = api.post(
        "/api/v1/auth/login",
        {"email": "wrongpw@example.com", "password": "incorrectpw99"},
        format="json",
    )
    assert resp.status_code == 401


# --- logout -----------------------------------------------------------------

def test_logout_requires_auth_and_returns_204(api):
    # Anonymous logout → 401/403 (IsAuthenticated).
    anon = api.post("/api/v1/auth/logout")
    assert anon.status_code in (401, 403)

    user = User.objects.create_user(
        email="logout@example.com", password="logoutpw1234", is_verified=True
    )
    api.force_authenticate(user=user)
    resp = api.post("/api/v1/auth/logout")
    assert resp.status_code == 204


# --- password reset ---------------------------------------------------------

def test_password_reset_always_202(api):
    # Unknown email still 202 (no enumeration).
    resp = api.post(
        "/api/v1/auth/password-reset", {"email": "ghost@example.com"}, format="json"
    )
    assert resp.status_code == 202


def test_password_reset_confirm_flow(api):
    user = User.objects.create_user(
        email="resetflow@example.com", password="origpasswd12", is_verified=True
    )
    mail.outbox.clear()
    api.post(
        "/api/v1/auth/password-reset", {"email": "resetflow@example.com"}, format="json"
    )
    token = _token_from_outbox()
    resp = api.post(
        "/api/v1/auth/password-reset/confirm",
        {"token": token, "password": "brandNewpw789"},
        format="json",
    )
    assert resp.status_code == 200
    user.refresh_from_db()
    assert user.check_password("brandNewpw789")


def test_password_reset_confirm_bad_token_400(api):
    resp = api.post(
        "/api/v1/auth/password-reset/confirm",
        {"token": "garbage", "password": "brandNewpw789"},
        format="json",
    )
    assert resp.status_code == 400


# --- /me --------------------------------------------------------------------

def test_me_requires_auth(api):
    resp = api.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)


def test_me_returns_user_and_memberships(api):
    user = User.objects.create_user(
        email="meuser@example.com", password="mepasswd1234", name="Me", is_verified=True
    )
    api.force_authenticate(user=user)
    resp = api.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "meuser@example.com"
    # No memberships yet (hasn't created/joined an org).
    assert body["memberships"] == []


def test_verify_token_namespaces_are_disjoint():
    """A reset token can't verify email and vice-versa (different signing salts)."""
    user = User.objects.create_user(
        email="ns@example.com", password="nspasswd1234", is_verified=False
    )
    verify_tok = tokens.make_email_verification_token(user)
    reset_tok = tokens.make_password_reset_token(user)
    assert tokens.read_email_verification_token(reset_tok) is None
    assert tokens.read_password_reset_token(verify_tok, get_user=lambda uid: user) is None
