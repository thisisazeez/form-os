"""
Two flows the live Chrome test surfaced:

  1. An invited user who signs up → verifies → logs in is AUTO-JOINED to the org, instead of
     landing on the create-org page (the signup→verify→login chain otherwise drops the invite
     token). apps.accounts.views_htmx.login_page → organizations.services.accept_pending_invitations.

  2. Org owner/admin can change a member's role from the Members page (op=change_role), with the
     RBAC invariants enforced (admins can't touch owners, the last owner can't be demoted,
     non-admins can't change anyone). organizations.services.change_member_role.

Org creation provisions a Postgres schema → transactional_db + force_drop cleanup. Invitations
are created directly (defaults fill token + expiry) so no email is sent under dev settings.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.organizations.enums import InviteStatus, MembershipStatus, OrgRole
from apps.organizations.models import Invitation, Membership, Organization
from apps.organizations.services import create_organization

User = get_user_model()

SLUG = "inviterole-org"
_EMAILS = ["ir_owner@example.com", "ir_invitee@example.com", "ir_member@example.com"]


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    """Org + owner. Yields (org, owner)."""
    connection.set_schema_to_public()
    User.objects.filter(email__in=_EMAILS).delete()
    owner = User.objects.create_user(
        email="ir_owner@example.com", password="x", is_verified=True
    )
    _cleanup(SLUG)
    org = create_organization(name="Invite Role Org", slug=SLUG, owner=owner)
    yield org, owner
    _cleanup(SLUG)


def _membership(user, org):
    with schema_context(get_public_schema_name()):
        return (
            Membership.objects.filter(
                user=user, organization=org, status=MembershipStatus.ACTIVE
            )
            .select_related("organization")
            .first()
        )


def _add_member(org, email, role=OrgRole.MEMBER):
    connection.set_schema_to_public()
    user = User.objects.create_user(email=email, password="x", is_verified=True)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


# --------------------------------------------------------------------------- #
#  Invite auto-accept on login                                                #
# --------------------------------------------------------------------------- #
def test_login_auto_accepts_pending_invitation(org_ctx):
    org, owner = org_ctx
    with schema_context(get_public_schema_name()):
        Invitation.objects.create(
            organization=org,
            email="ir_invitee@example.com",
            role=OrgRole.MEMBER,
            invited_by=owner,
        )
    invitee = User.objects.create_user(
        email="ir_invitee@example.com", password="sup3rsecretpw", is_verified=True
    )
    assert _membership(invitee, org) is None  # not a member before login

    resp = Client().post(
        "/auth/login",
        {"email": "ir_invitee@example.com", "password": "sup3rsecretpw"},
    )
    assert resp.status_code == 302
    # Auto-joined on login → membership exists with the invited role, and they land IN the org
    # (NOT the create-org page).
    m = _membership(invitee, org)
    assert m is not None and m.role == OrgRole.MEMBER
    assert resp["Location"] == f"/{SLUG}/"
    with schema_context(get_public_schema_name()):
        assert (
            Invitation.objects.get(email="ir_invitee@example.com").status
            == InviteStatus.ACCEPTED
        )


# --------------------------------------------------------------------------- #
#  Role change via the Members page                                           #
# --------------------------------------------------------------------------- #
def test_owner_changes_member_role(org_ctx):
    org, owner = org_ctx
    member = _add_member(org, "ir_member@example.com", OrgRole.MEMBER)
    m = _membership(member, org)
    c = Client()
    c.force_login(owner)
    resp = c.post(
        f"/{SLUG}/members",
        {"op": "change_role", "membership_id": str(m.id), "role": "admin"},
    )
    assert resp.status_code == 302
    assert resp["Location"] == f"/{SLUG}/members"
    assert _membership(member, org).role == OrgRole.ADMIN


def test_cannot_demote_last_owner(org_ctx):
    org, owner = org_ctx
    m_owner = _membership(owner, org)
    c = Client()
    c.force_login(owner)
    resp = c.post(
        f"/{SLUG}/members",
        {"op": "change_role", "membership_id": str(m_owner.id), "role": "member"},
    )
    # Last-owner invariant → page re-renders (200) with an error; role unchanged.
    assert resp.status_code == 200
    assert _membership(owner, org).role == OrgRole.OWNER


def test_member_cannot_change_roles(org_ctx):
    org, owner = org_ctx
    member = _add_member(org, "ir_member@example.com", OrgRole.MEMBER)
    m = _membership(member, org)
    c = Client()
    c.force_login(member)
    resp = c.post(
        f"/{SLUG}/members",
        {"op": "change_role", "membership_id": str(m.id), "role": "admin"},
    )
    assert resp.status_code == 403  # non-admins get the not-a-member page
    assert _membership(member, org).role == OrgRole.MEMBER
