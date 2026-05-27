"""
The unified org+workspace switcher's backend: the current workspace is session-backed and the
admin app scopes to it. `POST /<slug>/switch-workspace/<id>` sets it (RBAC-validated), and the
dashboard/surveys then show only that workspace's data.

apps.common.active_workspace + apps.workspaces.views_htmx.switch_workspace. Org creation
provisions a Postgres schema → transactional_db + force_drop cleanup.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client

from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace
from apps.workspaces.services import create_workspace

User = get_user_model()

SLUG = "switcher-org"
_LANGS = [{"code": "en", "label": "English", "default": True}]


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def two_ws_ctx(transactional_db):
    """Org + owner + two workspaces: 'Alpha WS' (has a survey) and 'Beta WS' (empty)."""
    connection.set_schema_to_public()
    User.objects.filter(email="sw_owner@example.com").delete()
    owner = User.objects.create_user(
        email="sw_owner@example.com", password="x", is_verified=True
    )
    _cleanup(SLUG)
    org = create_organization(name="Switcher Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    Workspace.objects.all().delete()  # drop the auto default; use two named ones
    ws_a = create_workspace(name="Alpha WS", slug=None, created_by_user_id=owner.id)
    ws_b = create_workspace(name="Beta WS", slug=None, created_by_user_id=owner.id)
    env_a = Environment.objects.filter(workspace=ws_a, type=EnvType.PRODUCTION).first()
    Survey.objects.create(
        environment=env_a,
        name="Alpha Survey",
        status=SurveyStatus.PUBLISHED,
        questions=[],
        languages=_LANGS,
        styling={},
        endings=[],
    )
    yield org, owner, ws_a, ws_b
    _cleanup(SLUG)


def test_dashboard_scopes_to_current_workspace(two_ws_ctx):
    org, owner, ws_a, ws_b = two_ws_ctx
    c = Client()
    c.force_login(owner)

    # Default current workspace = first by name ("Alpha WS") → its survey is on the dashboard.
    html = c.get(f"/{SLUG}/").content.decode()
    assert "Alpha Survey" in html

    # Switch to the empty "Beta WS" → the dashboard re-scopes; Alpha's survey is gone.
    resp = c.post(f"/{SLUG}/switch-workspace/{ws_b.id}")
    assert resp.status_code == 302
    assert resp["Location"] == f"/{SLUG}/"
    html_b = c.get(f"/{SLUG}/").content.decode()
    assert "Alpha Survey" not in html_b

    # Switch back to Alpha → survey reappears (session remembers per request).
    c.post(f"/{SLUG}/switch-workspace/{ws_a.id}")
    assert "Alpha Survey" in c.get(f"/{SLUG}/").content.decode()


def test_switch_workspace_rejects_unreadable_workspace(two_ws_ctx):
    """A member with no access to a workspace cannot make it current (RBAC) → 403, and the
    survey from a workspace they can't read never leaks onto their dashboard."""
    org, owner, ws_a, ws_b = two_ws_ctx
    # A plain org member with NO team grants can read no workspace.
    from django_tenants.utils import get_public_schema_name, schema_context

    from apps.organizations.enums import MembershipStatus, OrgRole
    from apps.organizations.models import Membership

    connection.set_schema_to_public()
    member = User.objects.create_user(
        email="sw_member@example.com", password="x", is_verified=True
    )
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=member, organization=org, role=OrgRole.MEMBER, status=MembershipStatus.ACTIVE
        )
    c = Client()
    c.force_login(member)
    resp = c.post(f"/{SLUG}/switch-workspace/{ws_a.id}")
    assert resp.status_code == 403
    # And the member's dashboard shows no survey (no readable workspace → empty scope).
    assert "Alpha Survey" not in c.get(f"/{SLUG}/").content.decode()
