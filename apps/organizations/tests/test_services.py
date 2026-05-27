"""
Service-layer tests for org creation, invitations, and membership management
(apps.organizations.services).

Org creation provisions a real Postgres schema (DDL), so these use ``transactional_db``
(not the plain atomic ``db`` fixture) — the same reason ``tests/conftest.py``'s
``demo_tenant`` does. We clean up provisioned schemas with ``force_drop=True``.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from apps.organizations import services
from apps.organizations.enums import InviteStatus, MembershipStatus, OrgRole
from apps.organizations.models import Domain, Invitation, Membership, Organization

User = get_user_model()


@pytest.fixture
def owner_user(transactional_db):
    connection.set_schema_to_public()
    User.objects.filter(email="owner@example.com").delete()
    return User.objects.create_user(
        email="owner@example.com", password="ownerpasswd123", name="Owner", is_verified=True
    )


def _cleanup_org(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


# --- org creation -----------------------------------------------------------

def test_create_organization_provisions_schema_domain_and_owner(owner_user):
    _cleanup_org("acme-co")
    try:
        org = services.create_organization(name="Acme Co", slug="acme-co", owner=owner_user)

        # Org + slug + derived schema_name (hyphen → underscore).
        assert org.slug == "acme-co"
        assert org.schema_name == "acme_co"

        # The Postgres schema physically exists.
        connection.set_schema_to_public()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                ["acme_co"],
            )
            assert cur.fetchone() is not None

        # A primary Domain row exists.
        assert Domain.objects.filter(tenant=org, is_primary=True).exists()

        # The creator is an active OWNER.
        m = Membership.objects.get(organization=org, user=owner_user)
        assert m.role == OrgRole.OWNER
        assert m.status == MembershipStatus.ACTIVE
    finally:
        _cleanup_org("acme-co")


@pytest.mark.parametrize("reserved", ["api", "auth", "s", "admin", "www"])
def test_create_organization_reserved_slug_rejected(owner_user, reserved):
    with pytest.raises(services.ReservedSlugError):
        services.create_organization(name="Evil", slug=reserved, owner=owner_user)

    # The reserved schema was NOT provisioned.
    connection.set_schema_to_public()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            [reserved.replace("-", "_")],
        )
        assert cur.fetchone() is None


def test_create_organization_duplicate_slug_rejected(owner_user):
    _cleanup_org("dupe-org")
    try:
        services.create_organization(name="First", slug="dupe-org", owner=owner_user)
        with pytest.raises(services.SlugTakenError):
            services.create_organization(name="Second", slug="dupe-org", owner=owner_user)
    finally:
        _cleanup_org("dupe-org")


# --- invitations ------------------------------------------------------------

@pytest.fixture
def org_with_owner(owner_user):
    _cleanup_org("invite-org")
    org = services.create_organization(name="Invite Org", slug="invite-org", owner=owner_user)
    yield org, owner_user
    _cleanup_org("invite-org")


def test_invite_member_creates_pending_invitation_and_email(org_with_owner):
    from django.core import mail

    org, owner = org_with_owner
    if hasattr(mail, "outbox"):
        mail.outbox.clear()

    inv = services.invite_member(
        organization=org, email="new@example.com", role=OrgRole.MEMBER, invited_by=owner
    )
    assert inv.status == InviteStatus.PENDING
    assert inv.email == "new@example.com"
    assert inv.role == OrgRole.MEMBER
    assert inv.token  # opaque token present (never returned via API, but on the model)
    if hasattr(mail, "outbox"):
        assert any("invited" in m.subject.lower() for m in mail.outbox)


def test_reinviting_supersedes_pending(org_with_owner):
    org, owner = org_with_owner
    first = services.invite_member(
        organization=org, email="dup@example.com", role=OrgRole.MEMBER, invited_by=owner
    )
    second = services.invite_member(
        organization=org, email="dup@example.com", role=OrgRole.ADMIN, invited_by=owner
    )
    first.refresh_from_db()
    assert first.status == InviteStatus.REVOKED
    assert second.status == InviteStatus.PENDING
    assert second.role == OrgRole.ADMIN
    # Only one pending invite for this (org, email).
    assert (
        Invitation.objects.filter(
            organization=org, email="dup@example.com", status=InviteStatus.PENDING
        ).count()
        == 1
    )


def test_invite_existing_member_rejected(org_with_owner):
    org, owner = org_with_owner
    with pytest.raises(services.DuplicatePendingInvitation):
        services.invite_member(
            organization=org, email=owner.email, role=OrgRole.MEMBER, invited_by=owner
        )


def test_accept_invitation_creates_membership(org_with_owner):
    org, owner = org_with_owner
    inv = services.invite_member(
        organization=org, email="invitee@example.com", role=OrgRole.ADMIN, invited_by=owner
    )
    connection.set_schema_to_public()
    invitee = User.objects.create_user(
        email="invitee@example.com", password="inviteepw1234", is_verified=True
    )

    membership = services.accept_invitation(token=inv.token, user=invitee)
    assert membership.role == OrgRole.ADMIN
    assert membership.organization_id == org.id

    inv.refresh_from_db()
    assert inv.status == InviteStatus.ACCEPTED
    assert inv.accepted_at is not None


def test_accept_invitation_email_mismatch_rejected(org_with_owner):
    org, owner = org_with_owner
    inv = services.invite_member(
        organization=org, email="target@example.com", role=OrgRole.MEMBER, invited_by=owner
    )
    connection.set_schema_to_public()
    other = User.objects.create_user(
        email="someoneelse@example.com", password="otherpw12345", is_verified=True
    )
    with pytest.raises(services.InvitationError):
        services.accept_invitation(token=inv.token, user=other)


def test_accept_invitation_expired_rejected(org_with_owner):
    from django.utils import timezone

    org, owner = org_with_owner
    inv = services.invite_member(
        organization=org, email="late@example.com", role=OrgRole.MEMBER, invited_by=owner
    )
    inv.expires_at = timezone.now() - timezone.timedelta(seconds=1)
    inv.save(update_fields=["expires_at"])

    connection.set_schema_to_public()
    invitee = User.objects.create_user(
        email="late@example.com", password="latepw123456", is_verified=True
    )
    with pytest.raises(services.InvitationError):
        services.accept_invitation(token=inv.token, user=invitee)
    inv.refresh_from_db()
    assert inv.status == InviteStatus.EXPIRED


def test_accept_invitation_bad_token_rejected(org_with_owner):
    org, owner = org_with_owner
    connection.set_schema_to_public()
    u = User.objects.create_user(
        email="x@example.com", password="xpassword123", is_verified=True
    )
    with pytest.raises(services.InvitationError):
        services.accept_invitation(token="garbage-token", user=u)


def test_revoke_invitation(org_with_owner):
    org, owner = org_with_owner
    inv = services.invite_member(
        organization=org, email="revokeme@example.com", role=OrgRole.MEMBER, invited_by=owner
    )
    services.revoke_invitation(organization=org, invitation_id=inv.id)
    inv.refresh_from_db()
    assert inv.status == InviteStatus.REVOKED


# --- membership management + last-owner invariant ---------------------------

def _add_member(org, email, role):
    connection.set_schema_to_public()
    user = User.objects.create_user(email=email, password="memberpw1234", is_verified=True)
    return Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
    )


def test_change_member_role_owner_can_promote_to_owner(org_with_owner):
    org, owner = org_with_owner
    owner_m = Membership.objects.get(organization=org, user=owner)
    target = _add_member(org, "member1@example.com", OrgRole.MEMBER)

    updated = services.change_member_role(
        organization=org, membership_id=target.id, new_role=OrgRole.OWNER,
        actor_membership=owner_m,
    )
    assert updated.role == OrgRole.OWNER


def test_admin_cannot_grant_ownership(org_with_owner):
    org, owner = org_with_owner
    admin_m = _add_member(org, "admin1@example.com", OrgRole.ADMIN)
    target = _add_member(org, "member2@example.com", OrgRole.MEMBER)

    with pytest.raises(services.OwnershipGrantError):
        services.change_member_role(
            organization=org, membership_id=target.id, new_role=OrgRole.OWNER,
            actor_membership=admin_m,
        )


def test_admin_cannot_change_owner_role(org_with_owner):
    org, owner = org_with_owner
    owner_m = Membership.objects.get(organization=org, user=owner)
    admin_m = _add_member(org, "admin2@example.com", OrgRole.ADMIN)

    with pytest.raises(services.OwnershipGrantError):
        services.change_member_role(
            organization=org, membership_id=owner_m.id, new_role=OrgRole.MEMBER,
            actor_membership=admin_m,
        )


def test_cannot_demote_last_owner(org_with_owner):
    org, owner = org_with_owner
    owner_m = Membership.objects.get(organization=org, user=owner)
    # Owner tries to demote themselves while being the only owner.
    with pytest.raises(services.LastOwnerError):
        services.change_member_role(
            organization=org, membership_id=owner_m.id, new_role=OrgRole.ADMIN,
            actor_membership=owner_m,
        )


def test_can_demote_owner_when_another_owner_exists(org_with_owner):
    org, owner = org_with_owner
    owner_m = Membership.objects.get(organization=org, user=owner)
    second_owner = _add_member(org, "owner2@example.com", OrgRole.OWNER)

    # Now demoting the first owner is allowed (a second owner remains).
    updated = services.change_member_role(
        organization=org, membership_id=owner_m.id, new_role=OrgRole.ADMIN,
        actor_membership=second_owner,
    )
    assert updated.role == OrgRole.ADMIN


def test_remove_member(org_with_owner):
    org, owner = org_with_owner
    owner_m = Membership.objects.get(organization=org, user=owner)
    target = _add_member(org, "removeme@example.com", OrgRole.MEMBER)

    services.remove_member(
        organization=org, membership_id=target.id, actor_membership=owner_m
    )
    assert not Membership.objects.filter(pk=target.id).exists()


def test_cannot_remove_last_owner(org_with_owner):
    org, owner = org_with_owner
    owner_m = Membership.objects.get(organization=org, user=owner)
    with pytest.raises(services.LastOwnerError):
        services.remove_member(
            organization=org, membership_id=owner_m.id, actor_membership=owner_m
        )


def test_admin_cannot_remove_owner(org_with_owner):
    org, owner = org_with_owner
    admin_m = _add_member(org, "admin3@example.com", OrgRole.ADMIN)
    owner_m = Membership.objects.get(organization=org, user=owner)
    with pytest.raises(services.OwnershipGrantError):
        services.remove_member(
            organization=org, membership_id=owner_m.id, actor_membership=admin_m
        )


# --- last-owner invariant under the locked path (concurrency regression) -----
#
# These guard the row-locked re-count added to close the last-owner TOCTOU race
# (auth-orgs reviewer Finding 1, rbac.md §2). The happy-path tests confirm the lock does
# not change the success behaviour; the threaded test proves two concurrent
# demotions/removals of *different* owners cannot both pass and drop the org to 0 owners.

def test_remove_owner_when_another_owner_exists(org_with_owner):
    """Locked path happy case: removing one of two owners succeeds and leaves ≥1 owner."""
    org, owner = org_with_owner
    owner_m = Membership.objects.get(organization=org, user=owner)
    second_owner = _add_member(org, "owner2remove@example.com", OrgRole.OWNER)

    services.remove_member(
        organization=org, membership_id=second_owner.id, actor_membership=owner_m
    )
    assert not Membership.objects.filter(pk=second_owner.id).exists()
    # The original owner remains → invariant held.
    assert services.active_owner_count(org) == 1


def _run_concurrent_owner_mutations(org, mutate, owner_a_id, owner_b_id):
    """Run ``mutate`` on two different owners from two threads at the same instant.

    ``mutate(membership_id)`` performs an owner-affecting service call (remove/demote) for
    the given owner. Each thread gets its own DB connection (Django opens one per thread);
    a ``Barrier`` lines both threads up so they hit the locked re-count together — the
    worst case for the TOCTOU race. Returns ``(successes, last_owner_errors)``.
    """
    import threading

    barrier = threading.Barrier(2)
    results = {"success": 0, "last_owner": 0}
    lock = threading.Lock()

    def worker(membership_id):
        connection.set_schema_to_public()
        try:
            barrier.wait(timeout=10)
            mutate(membership_id)
            with lock:
                results["success"] += 1
        except services.LastOwnerError:
            with lock:
                results["last_owner"] += 1
        finally:
            # Each thread owns its connection; close it so the locked transaction commits
            # and the connection is released back (mirrors demo_tenant's teardown hygiene).
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(owner_a_id,)),
        threading.Thread(target=worker, args=(owner_b_id,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    return results["success"], results["last_owner"]


def test_concurrent_remove_of_two_owners_keeps_one(org_with_owner):
    """TOCTOU regression: two threads removing *different* owners from a 2-owner org.

    Exactly one removal must succeed and one must be refused with ``LastOwnerError``; the
    org must still have ≥1 active owner afterward. Without the row lock, both threads would
    observe "1 other owner remains" and both delete → 0 owners (invariant violated).
    """
    org, owner = org_with_owner
    owner_a = Membership.objects.get(organization=org, user=owner)
    owner_b = _add_member(org, "owner2conc@example.com", OrgRole.OWNER)

    actor = owner_a  # an owner actor authorises removing another owner

    def mutate(membership_id):
        services.remove_member(
            organization=org, membership_id=membership_id, actor_membership=actor
        )

    successes, last_owner_errors = _run_concurrent_owner_mutations(
        org, mutate, owner_a.id, owner_b.id
    )

    connection.set_schema_to_public()
    assert successes == 1, f"expected exactly one removal to succeed, got {successes}"
    assert last_owner_errors == 1, (
        f"expected exactly one LastOwnerError, got {last_owner_errors}"
    )
    # The invariant: the org never dropped below one active owner.
    assert services.active_owner_count(org) == 1


def test_concurrent_demote_of_two_owners_keeps_one(org_with_owner):
    """TOCTOU regression (demotion path): two threads demoting *different* owners.

    Mirrors the removal race for ``change_member_role``: exactly one demotion succeeds, one
    is refused, and ≥1 active owner remains.
    """
    org, owner = org_with_owner
    owner_a = Membership.objects.get(organization=org, user=owner)
    owner_b = _add_member(org, "owner2demote@example.com", OrgRole.OWNER)

    actor = owner_a  # an owner actor may demote another owner

    def mutate(membership_id):
        services.change_member_role(
            organization=org,
            membership_id=membership_id,
            new_role=OrgRole.ADMIN,
            actor_membership=actor,
        )

    successes, last_owner_errors = _run_concurrent_owner_mutations(
        org, mutate, owner_a.id, owner_b.id
    )

    connection.set_schema_to_public()
    assert successes == 1, f"expected exactly one demotion to succeed, got {successes}"
    assert last_owner_errors == 1, (
        f"expected exactly one LastOwnerError, got {last_owner_errors}"
    )
    assert services.active_owner_count(org) == 1
