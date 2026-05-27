"""
HTTP-level tests for the ActionClass management API (FEATURE_PARITY WS-11 "Actions / Events").

DISTINCT DB: DATABASE_URL=postgres://opensurvey:opensurvey@localhost:5432/parity_appsurvey

Exercises the real request path through TenantSlugMiddleware: endpoints are hit at
``/api/v1/<slug>/...`` so the middleware resolves + activates the org schema and ``IsOrgMember``
+ ``can()`` run against the routed org. Org creation provisions a Postgres schema (DDL), so
tests use ``transactional_db`` + ``force_drop`` (same constraint as tests/conftest.py). Auth uses
DRF ``force_authenticate``.

Covers: create (config validation per type, name uniqueness 409, RBAC write), list (filtered to
readable workspaces), detail get/patch/delete (404-on-invisible, 403-on-unauthorized, config
re-validation on patch), and the read/write RBAC split (a read-only member can list/get but not
mutate).
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from apps.actions.models import ActionClass
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "actions-api-org"


@pytest.fixture
def api():
    return APIClient()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    """Org + owner + a workspace with a production Environment. Yields (org, owner, env)."""
    owner = _mk_user("actions_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Actions API Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


def _grant(org, user, workspace_id, access):
    connection.set_tenant(org)
    team = Team.objects.create(name=f"team-{user.email}-{access}")
    TeamMembership.objects.create(team=team, user_id=user.id)
    WorkspaceTeam.objects.create(team=team, workspace_id=workspace_id, access=access)


def _list_url(env_id):
    return f"/api/v1/{SLUG}/environments/{env_id}/actions"


def _detail_url(action_id):
    return f"/api/v1/{SLUG}/actions/{action_id}"


# --------------------------------------------------------------------------- #
#  Create
# --------------------------------------------------------------------------- #
def test_owner_creates_code_action(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(owner)
    resp = api.post(
        _list_url(env.id),
        {"name": "Clicked Upgrade", "type": "code", "config": {"key": "upgrade_clicked"}},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["name"] == "Clicked Upgrade"
    assert body["type"] == "code"
    assert body["config"] == {"key": "upgrade_clicked"}
    assert body["environment_id"] == str(env.id)
    # Persisted in the tenant schema.
    connection.set_tenant(org)
    assert ActionClass.objects.filter(environment=env, name="Clicked Upgrade").exists()


def test_create_pageview_and_click(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(owner)
    r1 = api.post(
        _list_url(env.id),
        {"name": "Pricing Page", "type": "pageview", "config": {"urlMatch": "/pricing"}},
        format="json",
    )
    assert r1.status_code == 201, r1.content
    r2 = api.post(
        _list_url(env.id),
        {"name": "CTA Click", "type": "click", "config": {"cssSelector": ".cta-btn"}},
        format="json",
    )
    assert r2.status_code == 201, r2.content


def test_create_rejects_unknown_config_key(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(owner)
    # urlMatch is not valid for a `code` action.
    resp = api.post(
        _list_url(env.id),
        {"name": "Bad", "type": "code", "config": {"urlMatch": "/x"}},
        format="json",
    )
    assert resp.status_code == 400, resp.content
    assert "errors" in resp.json()


def test_create_duplicate_name_409(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(owner)
    api.post(_list_url(env.id), {"name": "Dup", "type": "code"}, format="json")
    resp = api.post(_list_url(env.id), {"name": "Dup", "type": "code"}, format="json")
    assert resp.status_code == 409, resp.content


def test_member_without_write_cannot_create(api, org_ctx):
    org, owner, env = org_ctx
    reader = _add_member(org, "actions_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    api.force_authenticate(reader)
    resp = api.post(_list_url(env.id), {"name": "Nope", "type": "code"}, format="json")
    assert resp.status_code == 403, resp.content


def test_member_with_write_can_create(api, org_ctx):
    org, owner, env = org_ctx
    writer = _add_member(org, "actions_writer@example.com")
    _grant(org, writer, env.workspace_id, WorkspaceAccess.WRITE)
    api.force_authenticate(writer)
    resp = api.post(_list_url(env.id), {"name": "Yes", "type": "code"}, format="json")
    assert resp.status_code == 201, resp.content


# --------------------------------------------------------------------------- #
#  List (filtered)
# --------------------------------------------------------------------------- #
def test_list_returns_env_actions(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    ActionClass.objects.create(environment=env, name="A", type="code")
    ActionClass.objects.create(environment=env, name="B", type="code")
    api.force_authenticate(owner)
    resp = api.get(_list_url(env.id))
    assert resp.status_code == 200, resp.content
    names = sorted(a["name"] for a in resp.json())
    assert names == ["A", "B"]


def test_list_empty_for_member_without_access(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    ActionClass.objects.create(environment=env, name="Secret", type="code")
    outsider = _add_member(org, "actions_outsider@example.com")  # no workspace grant.
    api.force_authenticate(outsider)
    resp = api.get(_list_url(env.id))
    assert resp.status_code == 200
    assert resp.json() == []  # filtered to empty, not 403.


# --------------------------------------------------------------------------- #
#  Detail get / patch / delete
# --------------------------------------------------------------------------- #
def test_get_patch_delete(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    action = ActionClass.objects.create(
        environment=env, name="Edit Me", type="code", config={"key": "k1"}
    )
    api.force_authenticate(owner)

    # GET
    r = api.get(_detail_url(action.id))
    assert r.status_code == 200
    assert r.json()["name"] == "Edit Me"

    # PATCH name + config
    r = api.patch(
        _detail_url(action.id),
        {"name": "Renamed", "config": {"key": "k2"}},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["name"] == "Renamed"
    assert r.json()["config"] == {"key": "k2"}

    # DELETE
    r = api.delete(_detail_url(action.id))
    assert r.status_code == 204
    connection.set_tenant(org)
    assert not ActionClass.objects.filter(pk=action.id).exists()


def test_patch_type_switch_revalidates_config(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    action = ActionClass.objects.create(
        environment=env, name="Switch", type="code", config={"key": "k"}
    )
    api.force_authenticate(owner)
    # Switching to pageview while keeping the stale `key` config → 400 (key invalid for pageview).
    r = api.patch(_detail_url(action.id), {"type": "pageview"}, format="json")
    assert r.status_code == 400, r.content


def test_detail_404_for_invisible(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    action = ActionClass.objects.create(environment=env, name="Hidden", type="code")
    outsider = _add_member(org, "actions_outsider2@example.com")  # no grant.
    api.force_authenticate(outsider)
    r = api.get(_detail_url(action.id))
    assert r.status_code == 404  # no existence oracle.


def test_reader_cannot_patch_or_delete(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    action = ActionClass.objects.create(environment=env, name="RO", type="code")
    reader = _add_member(org, "actions_ro@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    api.force_authenticate(reader)
    assert api.patch(_detail_url(action.id), {"name": "X"}, format="json").status_code == 403
    assert api.delete(_detail_url(action.id)).status_code == 403
    # ...but CAN read.
    assert api.get(_detail_url(action.id)).status_code == 200
