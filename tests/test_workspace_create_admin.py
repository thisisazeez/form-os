"""
HTMX workspace-create page (``/<org_slug>/workspaces/new``) + the default-workspace guarantee
on org creation. Mirrors the Client + force_login + create_organization pattern of
tests/test_app_shell.py (org creation provisions a Postgres schema → transactional_db +
force_drop cleanup).

Coverage:
  - create_organization auto-provisions ONE "Default workspace" with production + development envs.
  - GET  /<slug>/workspaces/new renders for an org owner.
  - POST /<slug>/workspaces/new creates a workspace (+ both envs) and redirects to the dashboard.
  - A plain org member (no owner/admin role) is forbidden (403) — can(create_workspace) gate.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "wscreate-org"


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    """Org (+ its auto default workspace) + owner. Yields (org, owner)."""
    connection.set_schema_to_public()
    User.objects.filter(
        email__in=["wsc_owner@example.com", "wsc_member@example.com"]
    ).delete()
    owner = User.objects.create_user(
        email="wsc_owner@example.com", password="x", is_verified=True
    )
    _cleanup(SLUG)
    org = create_organization(name="WS Create Org", slug=SLUG, owner=owner)
    yield org, owner
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    connection.set_schema_to_public()
    user = User.objects.create_user(email=email, password="x", is_verified=True)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


def test_org_creation_provisions_default_workspace(org_ctx):
    org, owner = org_ctx
    connection.set_tenant(org)
    workspaces = list(Workspace.objects.all())
    assert len(workspaces) == 1
    assert workspaces[0].name == "Default workspace"
    env_types = set(
        Environment.objects.filter(workspace=workspaces[0]).values_list("type", flat=True)
    )
    assert env_types == {EnvType.PRODUCTION, EnvType.DEVELOPMENT}


def test_create_workspace_page_renders_for_owner(org_ctx):
    org, owner = org_ctx
    c = Client()
    c.force_login(owner)
    resp = c.get(f"/{SLUG}/workspaces/new")
    assert resp.status_code == 200
    assert b"Create a workspace" in resp.content


def test_create_workspace_page_creates_and_redirects(org_ctx):
    org, owner = org_ctx
    c = Client()
    c.force_login(owner)
    resp = c.post(f"/{SLUG}/workspaces/new", {"name": "Marketing"})
    assert resp.status_code == 302
    assert resp["Location"] == f"/{SLUG}/"
    connection.set_tenant(org)
    ws = Workspace.objects.get(name="Marketing")
    env_types = set(
        Environment.objects.filter(workspace=ws).values_list("type", flat=True)
    )
    assert env_types == {EnvType.PRODUCTION, EnvType.DEVELOPMENT}


def test_create_workspace_page_member_forbidden(org_ctx):
    org, owner = org_ctx
    member = _add_member(org, "wsc_member@example.com", OrgRole.MEMBER)
    c = Client()
    c.force_login(member)
    resp = c.post(f"/{SLUG}/workspaces/new", {"name": "Nope"})
    assert resp.status_code == 403
