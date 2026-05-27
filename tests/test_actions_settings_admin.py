"""
Management-UI tests for the Actions + Website/App Connection Settings tabs (FEATURE_PARITY WS-11).

These are server-rendered HTML pages (form-POST + post-redirect-get), workspace-scoped RBAC
(NOT org owner/admin like webhooks): viewing needs workspace read; create/edit/delete need
workspace write. The owner of an org has full workspace access; a plain member with NO workspace
grant has neither read nor write.

DISTINCT DB: DATABASE_URL=postgres://opensurvey:opensurvey@localhost:5432/parity_appui

Covers:
  - actions page: owner GET 200; create persists ActionClass (with type-specific config); a
    duplicate name surfaces a friendly error (no second row); update mutates; delete removes;
    a plain member (no workspace write) POSTing a create is 403 and does NOT create.
  - connection page: owner GET 200 renders the environment id + the exact embed snippet substring
    (the SHARED widget contract: /static/widget/opensurvey-widget.js + data-environment-id).

Django session Client + force_login (these are @login_required HTML views, not DRF). Org creation
provisions a schema (DDL) → transactional_db + force_drop (conftest.py). The org's auto-provisioned
default workspace + production Environment is the page's resolved target (current_workspace).
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.actions.models import ActionClass
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment

User = get_user_model()

SLUG = "actions-settings-org"


@pytest.fixture
def client():
    return Client()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True, name="U")


def _cleanup():
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=SLUG):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    owner = _mk_user("as_owner@example.com")
    _cleanup()
    org = create_organization(name="AS Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    # create_organization auto-provisions a "Default workspace" (+ prod/dev envs). The settings
    # page resolves the caller's CURRENT workspace (first readable by name) production env — use
    # that so the test mirrors what the page operates on.
    env = Environment.objects.filter(type=EnvType.PRODUCTION).order_by("workspace__name").first()
    yield org, owner, env
    _cleanup()


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


# --------------------------------------------------------------------------- #
#  Actions tab                                                                #
# --------------------------------------------------------------------------- #
def test_actions_page_owner_renders(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/actions")
    assert resp.status_code == 200
    # Owner has workspace write → the create form is shown.
    assert b"Add an action" in resp.content


def test_create_action_persists_with_config(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/settings/actions",
        {
            "op": "create",
            "name": "Checkout started",
            "type": "code",
            "config_value": "checkout_started",
            "description": "Fires on checkout",
        },
        follow=True,
    )
    assert resp.status_code == 200
    connection.set_tenant(org)
    ac = ActionClass.objects.filter(environment=env, name="Checkout started").first()
    assert ac is not None
    assert ac.type == "code"
    # The single config_value form field is mapped to the type's config key (code → key).
    assert ac.config == {"key": "checkout_started"}
    assert ac.description == "Fires on checkout"


def test_create_pageview_action_maps_config_to_urlmatch(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    client.post(
        f"/{SLUG}/settings/actions",
        {"op": "create", "name": "Viewed pricing", "type": "pageview", "config_value": "/pricing"},
        follow=True,
    )
    connection.set_tenant(org)
    ac = ActionClass.objects.get(environment=env, name="Viewed pricing")
    assert ac.type == "pageview"
    assert ac.config == {"urlMatch": "/pricing"}


def test_duplicate_name_does_not_create_second_row(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    from apps.actions import services as action_services

    action_services.create_action_class(environment=env, name="Dup", action_type="code")
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/settings/actions",
        {"op": "create", "name": "Dup", "type": "code"},
        follow=True,
    )
    assert resp.status_code == 200
    # A friendly error is surfaced and NO second row is created.
    assert b"already exists" in resp.content
    connection.set_tenant(org)
    assert ActionClass.objects.filter(environment=env, name="Dup").count() == 1


def test_update_action(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    from apps.actions import services as action_services

    ac = action_services.create_action_class(
        environment=env, name="Old name", action_type="code", config={"key": "old_key"}
    )
    client.force_login(owner)
    client.post(
        f"/{SLUG}/settings/actions",
        {
            "op": "update",
            "action_id": str(ac.id),
            "name": "New name",
            "type": "click",
            "config_value": "#buy",
        },
        follow=True,
    )
    connection.set_tenant(org)
    ac.refresh_from_db()
    assert ac.name == "New name"
    assert ac.type == "click"
    # The config is re-validated against the NEW type (code key dropped; click selector set).
    assert ac.config == {"cssSelector": "#buy"}


def test_delete_action(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    from apps.actions import services as action_services

    ac = action_services.create_action_class(environment=env, name="To delete", action_type="code")
    client.force_login(owner)
    client.post(
        f"/{SLUG}/settings/actions",
        {"op": "delete", "action_id": str(ac.id)},
        follow=True,
    )
    connection.set_tenant(org)
    assert not ActionClass.objects.filter(pk=ac.id).exists()


def test_member_without_workspace_write_cannot_create(client, org_ctx):
    org, owner, env = org_ctx
    member = _add_member(org, "as_member@example.com")
    client.force_login(member)
    resp = client.post(
        f"/{SLUG}/settings/actions",
        {"op": "create", "name": "Sneaky", "type": "code"},
    )
    # No workspace write → 403 (the page renders the not-a-member 403 body).
    assert resp.status_code == 403
    connection.set_tenant(org)
    assert not ActionClass.objects.filter(environment=env, name="Sneaky").exists()


# --------------------------------------------------------------------------- #
#  Website & App Connection tab                                               #
# --------------------------------------------------------------------------- #
def test_connection_page_renders_env_id_and_snippet(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/connection")
    assert resp.status_code == 200
    body = resp.content.decode()
    # The environment id is shown (in the Environment ID code block).
    assert str(env.id) in body
    # THE SHARED CONTRACT snippet (must match the widget agent exactly). The snippet is rendered
    # inside <code> for DISPLAY, so Django auto-escapes the angle brackets + quotes; the copy
    # button copies the un-escaped textContent. We assert the ESCAPED rendered form (what a browser
    # actually shows) AND the raw context value (the source of truth handed to the view).
    assert "/static/widget/opensurvey-widget.js" in body  # survives escaping (no special chars)
    assert f"data-environment-id=&quot;{env.id}&quot;" in body  # escaped attribute in the <code>
    # The exact RAW snippet string the view builds (the SHARED widget contract). We assert it on the
    # view context, which is the un-escaped string the copy button yields and the widget agent reads.
    expected_snippet = (
        f'<script src="http://testserver/static/widget/opensurvey-widget.js" '
        f'data-environment-id="{env.id}" '
        f'data-org-slug="{org.slug}"></script>'
    )
    assert resp.context["embed_snippet"] == expected_snippet
    # The JS usage notes are present.
    assert "window.openSurvey.setUserId" in body
    assert "window.openSurvey.track" in body


def test_connection_page_member_without_access_sees_locked_state(client, org_ctx):
    org, owner, env = org_ctx
    member = _add_member(org, "as_member2@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/settings/connection")
    # A member with no workspace read sees the locked/no-workspace state (NOT the env id).
    assert resp.status_code == 200
    assert str(env.id) not in resp.content.decode()
