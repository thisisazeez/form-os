"""
Tests for the server-rendered (HTMX) auth + org pages.

These hit the pages with Django's test ``Client`` (CSRF checks are disabled by default in
the test client, which is what we want for POST-form coverage). They confirm the pages
render, that form posts drive the SAME services as the API, and that org-level RBAC gates
the members page.

Public pages run on the public schema; the tenant members page is exercised against a
provisioned org (transactional_db, force_drop cleanup) just like the API tests.
"""
import re
from urllib.parse import unquote

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test import Client

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Invitation, Membership, Organization
from apps.organizations.services import create_organization, invite_member

User = get_user_model()


def _token_from_outbox():
    assert mail.outbox
    m = re.search(r"[?&]token=(\S+)", mail.outbox[-1].body)
    assert m
    return unquote(m.group(1))


# ---------------------------------------------------------------------------
# Public auth pages
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_signup_page_get_renders():
    resp = Client().get("/auth/signup")
    assert resp.status_code == 200
    assert b"Create your account" in resp.content


@pytest.mark.django_db
def test_signup_page_post_creates_user_and_shows_done():
    mail.outbox.clear()
    resp = Client().post(
        "/auth/signup",
        {"name": "Page User", "email": "pageuser@example.com", "password": "sup3rsecretpw"},
    )
    assert resp.status_code == 200
    assert b"verification link" in resp.content
    user = User.objects.get(email="pageuser@example.com")
    assert user.is_verified is False
    assert any("Verify" in m.subject for m in mail.outbox)


@pytest.mark.django_db
def test_signup_page_duplicate_shows_field_error():
    User.objects.create_user(email="dupe@example.com", password="sup3rsecretpw")
    resp = Client().post(
        "/auth/signup",
        {"email": "dupe@example.com", "password": "anotherpw1234"},
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.content


@pytest.mark.django_db
def test_login_page_unverified_shows_error():
    User.objects.create_user(
        email="unverifiedpage@example.com", password="sup3rsecretpw", is_verified=False
    )
    resp = Client().post(
        "/auth/login",
        {"email": "unverifiedpage@example.com", "password": "sup3rsecretpw"},
    )
    assert resp.status_code == 200
    assert b"Invalid credentials or unverified email" in resp.content


@pytest.mark.django_db
def test_login_page_success_redirects_to_create_org_when_no_membership():
    # A verified user with no org membership lands on the create-org page (NOT the old
    # public landing page) — apps.accounts.views_htmx._post_login_redirect.
    User.objects.create_user(
        email="loginpage@example.com", password="sup3rsecretpw", is_verified=True
    )
    resp = Client().post(
        "/auth/login",
        {"email": "loginpage@example.com", "password": "sup3rsecretpw"},
    )
    assert resp.status_code == 302
    assert resp["Location"] == "/auth/organizations/new"


@pytest.mark.django_db
def test_login_page_rejects_open_redirect_next():
    """An off-host ?next= is ignored (open-redirect guard) — falls back to the in-app
    destination (create-org for a no-membership user), never the attacker host."""
    User.objects.create_user(
        email="redir@example.com", password="sup3rsecretpw", is_verified=True
    )
    resp = Client().post(
        "/auth/login?next=https://evil.example.com/phish",
        {"email": "redir@example.com", "password": "sup3rsecretpw"},
    )
    assert resp.status_code == 302
    assert "evil.example.com" not in resp["Location"]
    assert resp["Location"] == "/auth/organizations/new"


@pytest.mark.django_db
def test_auth_pages_redirect_when_already_authenticated():
    """A logged-in user hitting /auth/login or /auth/signup is bounced into the app, NOT shown
    the form again (apps.accounts.views_htmx login_page/signup_page is_authenticated guard)."""
    user = User.objects.create_user(
        email="alreadyin@example.com", password="sup3rsecretpw", is_verified=True
    )
    client = Client()
    client.force_login(user)
    login_resp = client.get("/auth/login")
    assert login_resp.status_code == 302
    assert login_resp["Location"] != "/auth/login"  # no membership → create-org page
    signup_resp = client.get("/auth/signup")
    assert signup_resp.status_code == 302
    # Anonymous still sees the form (the guard only affects authenticated requests).
    assert Client().get("/auth/login").status_code == 200


@pytest.mark.django_db
def test_verify_email_page_success():
    from apps.accounts import tokens

    user = User.objects.create_user(
        email="verifypage@example.com", password="sup3rsecretpw", is_verified=False
    )
    token = tokens.make_email_verification_token(user)
    resp = Client().get(f"/auth/verify-email?token={token}")
    assert resp.status_code == 200
    assert b"Email verified" in resp.content
    user.refresh_from_db()
    assert user.is_verified is True


@pytest.mark.django_db
def test_verify_email_page_bad_token():
    resp = Client().get("/auth/verify-email?token=garbage")
    assert resp.status_code == 200
    assert b"Verification failed" in resp.content


@pytest.mark.django_db
def test_password_reset_request_page_always_succeeds():
    resp = Client().post("/auth/reset-password", {"email": "whoever@example.com"})
    assert resp.status_code == 200
    assert b"we've sent a reset link" in resp.content.lower()


@pytest.mark.django_db
def test_password_reset_confirm_page_flow():
    user = User.objects.create_user(
        email="resetpage@example.com", password="origpasswd12", is_verified=True
    )
    mail.outbox.clear()
    Client().post("/auth/reset-password", {"email": "resetpage@example.com"})
    token = _token_from_outbox()

    # GET the confirm form (token in query).
    resp = Client().get(f"/auth/reset-password?token={token}")
    assert resp.status_code == 200
    assert b"Set a new password" in resp.content

    # POST the new password.
    resp = Client().post(
        "/auth/reset-password", {"token": token, "password": "brandNewpw789"}
    )
    assert resp.status_code == 200
    assert b"password has been updated" in resp.content.lower()
    user.refresh_from_db()
    assert user.check_password("brandNewpw789")


# ---------------------------------------------------------------------------
# Org create + invitation accept (public) + members (tenant)
# ---------------------------------------------------------------------------
def _mk_user(email, **extra):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(
        email=email, password="testpasswd123", is_verified=True, **extra
    )


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.mark.django_db(transaction=True)
def test_create_org_page_requires_login():
    resp = Client().get("/auth/organizations/new")
    # login_required → redirect to LOGIN_URL (default /accounts/login/...).
    assert resp.status_code in (302, 301)


@pytest.mark.django_db(transaction=True)
def test_create_org_page_post_creates_org():
    owner = _mk_user("orgpageowner@example.com")
    _cleanup("page-org")
    client = Client()
    client.force_login(owner)
    try:
        resp = client.post(
            "/auth/organizations/new", {"name": "Page Org", "slug": "page-org"}
        )
        assert resp.status_code == 302  # → /page-org/
        connection.set_schema_to_public()
        org = Organization.objects.get(slug="page-org")
        assert Membership.objects.filter(organization=org, user=owner, role=OrgRole.OWNER).exists()
    finally:
        _cleanup("page-org")


@pytest.mark.django_db(transaction=True)
def test_create_org_page_reserved_slug_shows_error():
    owner = _mk_user("reservedpage@example.com")
    client = Client()
    client.force_login(owner)
    resp = client.post("/auth/organizations/new", {"name": "Bad", "slug": "api"})
    assert resp.status_code == 200
    assert b"reserved" in resp.content.lower()


@pytest.mark.django_db(transaction=True)
def test_accept_invitation_page_anonymous_prompts_auth():
    owner = _mk_user("inviteowner@example.com")
    _cleanup("inv-page-org")
    try:
        org = create_organization(name="Inv Page Org", slug="inv-page-org", owner=owner)
        inv = invite_member(
            organization=org, email="pendinginvitee@example.com",
            role=OrgRole.MEMBER, invited_by=owner,
        )
        # Anonymous → page shows the invite + prompts login/signup.
        resp = Client().get(f"/auth/invitations/accept?token={inv.token}")
        assert resp.status_code == 200
        assert b"Inv Page Org" in resp.content
        assert b"Log in" in resp.content
    finally:
        _cleanup("inv-page-org")


@pytest.mark.django_db(transaction=True)
def test_accept_invitation_page_authenticated_accepts():
    owner = _mk_user("inviteowner2@example.com")
    _cleanup("inv-page-org2")
    try:
        org = create_organization(name="Inv Page Org2", slug="inv-page-org2", owner=owner)
        inv = invite_member(
            organization=org, email="joinerpage@example.com",
            role=OrgRole.ADMIN, invited_by=owner,
        )
        joiner = _mk_user("joinerpage@example.com")
        client = Client()
        client.force_login(joiner)
        resp = client.post("/auth/invitations/accept", {"token": inv.token})
        assert resp.status_code == 302  # → /inv-page-org2/
        connection.set_schema_to_public()
        assert Membership.objects.filter(organization=org, user=joiner, role=OrgRole.ADMIN).exists()
        inv.refresh_from_db()
        assert inv.status == "accepted"
    finally:
        _cleanup("inv-page-org2")


@pytest.mark.django_db(transaction=True)
def test_members_page_lists_members_and_invite_form_for_admin():
    owner = _mk_user("memberspageowner@example.com")
    _cleanup("members-page-org")
    try:
        org = create_organization(name="Members Page Org", slug="members-page-org", owner=owner)
        client = Client()
        client.force_login(owner)
        resp = client.get(f"/{org.slug}/members")
        assert resp.status_code == 200
        assert b"memberspageowner@example.com" in resp.content
        # Owner sees the invite form.
        assert b"Invite a member" in resp.content
    finally:
        _cleanup("members-page-org")


@pytest.mark.django_db(transaction=True)
def test_members_page_member_cannot_see_invite_form():
    owner = _mk_user("mpo2@example.com")
    _cleanup("members-page-org2")
    try:
        org = create_organization(name="MPO2", slug="members-page-org2", owner=owner)
        member = _mk_user("plainmpo2@example.com")
        connection.set_schema_to_public()
        Membership.objects.create(
            user=member, organization=org, role=OrgRole.MEMBER,
            status=MembershipStatus.ACTIVE,
        )
        client = Client()
        client.force_login(member)
        resp = client.get(f"/{org.slug}/members")
        assert resp.status_code == 200
        # Plain member: no invite form.
        assert b"Invite a member" not in resp.content
    finally:
        _cleanup("members-page-org2")


@pytest.mark.django_db(transaction=True)
def test_members_page_non_member_403():
    owner = _mk_user("mpo3@example.com")
    _cleanup("members-page-org3")
    try:
        org = create_organization(name="MPO3", slug="members-page-org3", owner=owner)
        outsider = _mk_user("outsidermpo3@example.com")
        client = Client()
        client.force_login(outsider)
        resp = client.get(f"/{org.slug}/members")
        assert resp.status_code == 403
    finally:
        _cleanup("members-page-org3")


@pytest.mark.django_db(transaction=True)
def test_members_page_invite_via_form_creates_invitation():
    owner = _mk_user("mpo4@example.com")
    _cleanup("members-page-org4")
    try:
        org = create_organization(name="MPO4", slug="members-page-org4", owner=owner)
        client = Client()
        client.force_login(owner)
        mail.outbox.clear()
        resp = client.post(
            f"/{org.slug}/members",
            {"email": "viaform@example.com", "role": OrgRole.MEMBER},
        )
        assert resp.status_code == 302  # redirect back to members
        connection.set_schema_to_public()
        assert Invitation.objects.filter(organization=org, email="viaform@example.com").exists()
        assert any("invited" in m.subject.lower() for m in mail.outbox)
    finally:
        _cleanup("members-page-org4")
