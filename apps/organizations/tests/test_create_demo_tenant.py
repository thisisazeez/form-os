"""The Render startup command must actually create the documented demo login."""
from io import StringIO

from django.core.management import call_command
from django_tenants.utils import tenant_context

from apps.accounts.models import User
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.workspaces.models import Workspace


def test_create_demo_tenant_seeds_verified_owner_and_workspace(db):
    call_command("create_demo_tenant", stdout=StringIO())

    user = User.objects.get(email="demo@onform.example")
    assert user.is_verified is True
    assert user.is_active is True
    assert user.check_password("DemoPass123!") is True

    org = Organization.objects.get(slug="demo")
    assert Membership.objects.filter(
        user=user,
        organization=org,
        role=OrgRole.OWNER,
        status=MembershipStatus.ACTIVE,
    ).exists()

    with tenant_context(org):
        assert Workspace.objects.count() == 1


def test_create_demo_tenant_is_idempotent(db):
    call_command("create_demo_tenant", stdout=StringIO())
    call_command("create_demo_tenant", stdout=StringIO())

    assert User.objects.filter(email="demo@onform.example").count() == 1
    assert Organization.objects.filter(slug="demo").count() == 1
    assert Membership.objects.filter(user__email="demo@onform.example").count() == 1
