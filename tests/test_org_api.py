"""
HTTP-level tests for org creation, settings, members, and invitations
(contracts/openapi.yaml — Organizations tag).

These exercise the real request path including TenantSlugMiddleware: org-scoped endpoints
are hit at ``/api/v1/<slug>/...`` so the middleware resolves + activates the org schema and
sets ``request.tenant`` before the org-level permission classes run.

Org creation provisions a Postgres schema (DDL), so tests use ``transactional_db`` (the
same constraint as tests/conftest.py's demo_tenant) and clean up with ``force_drop=True``.

Auth uses DRF ``force_authenticate`` (SessionAuthentication's CSRF enforcement only fires
for authenticated requests; force_authenticate sidesteps it cleanly in tests).

NOTE: the public-schema routes are shipped under ``/api/v1/auth/...`` (org creation +
invitation acceptance) due to the documented middleware deviation — see SUMMARY.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.test import APIClient

from apps.organizations.enums import InviteStatus, MembershipStatus, OrgRole
from apps.organizations.models import Invitation, Membership, Organization
from apps.organizations.services import create_organization

User = get_user_model()


@pytest.fixture
def api():
    return APIClient()


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


@pytest.fixture
def org_ctx(transactional_db):
    """Provision an org with an owner; yield (org, owner). Cleans up the schema after."""
    owner = _mk_user("ownerapi@example.com")
    _cleanup("api-org")
    org = create_organization(name="API Org", slug="api-org", owner=owner)
    yield org, owner
    _cleanup("api-org")


# --- org creation (public, /api/v1/auth/organizations) ----------------------

def test_create_org_endpoint_creates_org_domain_owner(api, transactional_db):
    owner = _mk_user("creator@example.com")
    _cleanup("created-org")
    api.force_authenticate(user=owner)
    try:
        resp = api.post(
            "/api/v1/auth/organizations",
            {"name": "Created Org", "slug": "created-org"},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["slug"] == "created-org"
        assert set(body) == {"id", "name", "slug", "created_at"}  # openapi Organization

        connection.set_schema_to_public()
        org = Organization.objects.get(slug="created-org")
        # Owner membership created.
        m = Membership.objects.get(organization=org, user=owner)
        assert m.role == OrgRole.OWNER and m.status == MembershipStatus.ACTIVE
        # Schema physically provisioned.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                ["created_org"],
            )
            assert cur.fetchone() is not None
    finally:
        _cleanup("created-org")


def test_create_org_reserved_slug_returns_400(api, transactional_db):
    owner = _mk_user("reserved@example.com")
    api.force_authenticate(user=owner)
    resp = api.post(
        "/api/v1/auth/organizations",
        {"name": "Bad", "slug": "api"},
        format="json",
    )
    # Reserved slug → DRF SlugField also rejects? 'api' is a valid slug shape, so the
    # service's reserved-slug rule fires → 400.
    assert resp.status_code == 400
    connection.set_schema_to_public()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", ["api"]
        )
        assert cur.fetchone() is None


def test_create_org_requires_auth(api, transactional_db):
    resp = api.post(
        "/api/v1/auth/organizations",
        {"name": "NoAuth", "slug": "noauth-org"},
        format="json",
    )
    assert resp.status_code in (401, 403)


# --- org settings (tenant, /api/v1/<slug>/org) ------------------------------

def test_get_org_as_member(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{org.slug}/org")
    assert resp.status_code == 200
    assert resp.json()["slug"] == org.slug


def test_get_org_non_member_403(api, org_ctx):
    org, owner = org_ctx
    outsider = _mk_user("outsider@example.com")
    api.force_authenticate(user=outsider)
    resp = api.get(f"/api/v1/{org.slug}/org")
    assert resp.status_code == 403


def test_patch_org_name_as_owner(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    resp = api.patch(f"/api/v1/{org.slug}/org", {"name": "Renamed Org"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed Org"


def test_patch_org_as_member_forbidden_403(api, org_ctx):
    org, owner = org_ctx
    # Add a plain member.
    member = _mk_user("plainmember@example.com")
    connection.set_schema_to_public()
    Membership.objects.create(
        user=member, organization=org, role=OrgRole.MEMBER, status=MembershipStatus.ACTIVE
    )
    api.force_authenticate(user=member)
    resp = api.patch(f"/api/v1/{org.slug}/org", {"name": "Hacked"}, format="json")
    assert resp.status_code == 403


# --- members list / role change / remove ------------------------------------

def test_list_members_as_member(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{org.slug}/members")
    assert resp.status_code == 200
    body = resp.json()
    # openapi Paginated shape = {count, page, page_size, results}.
    assert set(body) == {"count", "page", "page_size", "results"}
    assert body["count"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 50  # openapi PageSize default
    assert body["results"][0]["user"]["email"] == owner.email
    assert body["results"][0]["role"] == OrgRole.OWNER


def test_change_member_role_owner_to_admin(api, org_ctx):
    org, owner = org_ctx
    member = _mk_user("promoteme@example.com")
    connection.set_schema_to_public()
    m = Membership.objects.create(
        user=member, organization=org, role=OrgRole.MEMBER, status=MembershipStatus.ACTIVE
    )
    api.force_authenticate(user=owner)
    resp = api.patch(
        f"/api/v1/{org.slug}/members/{m.id}", {"role": OrgRole.ADMIN}, format="json"
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == OrgRole.ADMIN


def test_admin_cannot_grant_ownership_409(api, org_ctx):
    org, owner = org_ctx
    connection.set_schema_to_public()
    admin = _mk_user("adminapi@example.com")
    Membership.objects.create(
        user=admin, organization=org, role=OrgRole.ADMIN, status=MembershipStatus.ACTIVE
    )
    target = _mk_user("targetapi@example.com")
    tm = Membership.objects.create(
        user=target, organization=org, role=OrgRole.MEMBER, status=MembershipStatus.ACTIVE
    )
    api.force_authenticate(user=admin)
    resp = api.patch(
        f"/api/v1/{org.slug}/members/{tm.id}", {"role": OrgRole.OWNER}, format="json"
    )
    assert resp.status_code == 409


def test_cannot_demote_last_owner_409(api, org_ctx):
    org, owner = org_ctx
    connection.set_schema_to_public()
    owner_m = Membership.objects.get(organization=org, user=owner)
    api.force_authenticate(user=owner)
    resp = api.patch(
        f"/api/v1/{org.slug}/members/{owner_m.id}", {"role": OrgRole.ADMIN}, format="json"
    )
    assert resp.status_code == 409


def test_remove_member(api, org_ctx):
    org, owner = org_ctx
    connection.set_schema_to_public()
    member = _mk_user("removeapi@example.com")
    m = Membership.objects.create(
        user=member, organization=org, role=OrgRole.MEMBER, status=MembershipStatus.ACTIVE
    )
    api.force_authenticate(user=owner)
    resp = api.delete(f"/api/v1/{org.slug}/members/{m.id}")
    assert resp.status_code == 204
    connection.set_schema_to_public()
    assert not Membership.objects.filter(pk=m.id).exists()


def test_cannot_remove_last_owner_409(api, org_ctx):
    org, owner = org_ctx
    connection.set_schema_to_public()
    owner_m = Membership.objects.get(organization=org, user=owner)
    api.force_authenticate(user=owner)
    resp = api.delete(f"/api/v1/{org.slug}/members/{owner_m.id}")
    assert resp.status_code == 409


def test_member_cannot_remove_member_403(api, org_ctx):
    org, owner = org_ctx
    connection.set_schema_to_public()
    m1 = Membership.objects.create(
        user=_mk_user("m1@example.com"), organization=org, role=OrgRole.MEMBER,
        status=MembershipStatus.ACTIVE,
    )
    m2 = Membership.objects.create(
        user=_mk_user("m2@example.com"), organization=org, role=OrgRole.MEMBER,
        status=MembershipStatus.ACTIVE,
    )
    api.force_authenticate(user=m1.user)
    resp = api.delete(f"/api/v1/{org.slug}/members/{m2.id}")
    assert resp.status_code == 403


# --- invitations: create / list / revoke / accept --------------------------

def test_invite_member_as_owner_201(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": "invitee@example.com", "role": OrgRole.MEMBER},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["email"] == "invitee@example.com"
    assert body["status"] == InviteStatus.PENDING
    # Token NEVER returned in the API response (openapi note).
    assert "token" not in body


def test_invite_member_as_plain_member_403(api, org_ctx):
    org, owner = org_ctx
    connection.set_schema_to_public()
    member = _mk_user("inviteblocked@example.com")
    Membership.objects.create(
        user=member, organization=org, role=OrgRole.MEMBER, status=MembershipStatus.ACTIVE
    )
    api.force_authenticate(user=member)
    resp = api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": "x@example.com", "role": OrgRole.MEMBER},
        format="json",
    )
    assert resp.status_code == 403


def test_invite_existing_member_409(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    # Inviting an existing member's email → 409 (already a member).
    resp = api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": owner.email, "role": OrgRole.MEMBER},
        format="json",
    )
    assert resp.status_code == 409


def test_reinvite_pending_supersedes_returns_201(api, org_ctx):
    """models.md §1.5: re-inviting a still-pending email SUPERSEDES (NOT 409).

    The openapi terse line ("409: pending invitation exists") is overridden by models.md's
    explicit "re-inviting supersedes" — see SUMMARY openapi ambiguities. So a second invite
    to the same email returns 201 and the prior pending invite is revoked.
    """
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    first = api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": "resend@example.com", "role": OrgRole.MEMBER},
        format="json",
    )
    assert first.status_code == 201
    second = api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": "resend@example.com", "role": OrgRole.ADMIN},
        format="json",
    )
    assert second.status_code == 201  # superseded, not rejected
    assert second.json()["role"] == OrgRole.ADMIN
    connection.set_schema_to_public()
    # Exactly one pending invite remains for this (org, email).
    assert (
        Invitation.objects.filter(
            organization=org, email="resend@example.com", status=InviteStatus.PENDING
        ).count()
        == 1
    )


def test_list_invitations(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": "listme@example.com", "role": OrgRole.ADMIN},
        format="json",
    )
    resp = api.get(f"/api/v1/{org.slug}/invitations")
    assert resp.status_code == 200
    data = resp.json()
    assert any(i["email"] == "listme@example.com" for i in data)


def test_revoke_invitation_204(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    created = api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": "revoketarget@example.com", "role": OrgRole.MEMBER},
        format="json",
    ).json()
    resp = api.delete(f"/api/v1/{org.slug}/invitations/{created['id']}")
    assert resp.status_code == 204
    connection.set_schema_to_public()
    assert Invitation.objects.get(pk=created["id"]).status == InviteStatus.REVOKED


def test_accept_invitation_creates_membership(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": "joiner@example.com", "role": OrgRole.ADMIN},
        format="json",
    )
    connection.set_schema_to_public()
    invitation = Invitation.objects.get(organization=org, email="joiner@example.com")
    joiner = _mk_user("joiner@example.com")

    # Accept as the invited user (public endpoint, shipped under auth/).
    api2 = APIClient()
    api2.force_authenticate(user=joiner)
    resp = api2.post(
        "/api/v1/auth/invitations/accept", {"token": invitation.token}, format="json"
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["role"] == OrgRole.ADMIN
    connection.set_schema_to_public()
    assert Membership.objects.filter(organization=org, user=joiner).exists()


def test_accept_invitation_email_mismatch_400(api, org_ctx):
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    api.post(
        f"/api/v1/{org.slug}/invitations",
        {"email": "intended@example.com", "role": OrgRole.MEMBER},
        format="json",
    )
    connection.set_schema_to_public()
    invitation = Invitation.objects.get(organization=org, email="intended@example.com")
    wrong = _mk_user("wrongperson@example.com")

    api2 = APIClient()
    api2.force_authenticate(user=wrong)
    resp = api2.post(
        "/api/v1/auth/invitations/accept", {"token": invitation.token}, format="json"
    )
    assert resp.status_code == 400


def test_me_lists_membership_after_join(api, org_ctx):
    """End-to-end: after accepting, /me reflects the new membership (org switcher)."""
    org, owner = org_ctx
    api.force_authenticate(user=owner)
    resp = api.get("/api/v1/auth/me")
    assert resp.status_code == 200
    memberships = resp.json()["memberships"]
    assert len(memberships) == 1
    assert memberships[0]["organization_slug"] == org.slug
    assert memberships[0]["role"] == OrgRole.OWNER
