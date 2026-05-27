"""
Management-UI tests for the API-keys + Webhooks Settings tabs (server-rendered HTML).

Covers: owner can open + create (the secret is revealed ONCE), revoke/delete; a plain member is
403; the create flow writes an audit entry. Webhooks: owner can create (secret revealed once),
the endpoint persists with its events, delete works; member 403.

Django session Client + force_login (these are @login_required HTML views, not DRF). Org
creation provisions a schema (DDL) → transactional_db + force_drop (conftest.py). A workspace +
production Environment exist so the keys/webhooks have a target environment.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.apikeys.models import ApiKey
from apps.audit.models import AuditLog
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.webhooks.enums import WebhookEvent
from apps.webhooks.models import Webhook
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment

User = get_user_model()

SLUG = "platform-settings-org"


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
    owner = _mk_user("ps_owner@example.com")
    _cleanup()
    org = create_organization(name="PS Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    # create_organization auto-provisions a "Default workspace" (+ prod/dev envs). The settings
    # pages operate on the caller's CURRENT workspace (first readable by name) — use that env so
    # the test mirrors what the page resolves, not a second orphan workspace.
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
#  API keys tab                                                               #
# --------------------------------------------------------------------------- #
def test_api_keys_page_owner_renders(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/api-keys")
    assert resp.status_code == 200
    assert b"Create an API key" in resp.content


def test_api_keys_page_member_403(client, org_ctx):
    org, owner, env = org_ctx
    member = _add_member(org, "ps_member@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/settings/api-keys")
    assert resp.status_code == 403


def test_create_api_key_reveals_secret_once_and_audits(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)

    resp = client.post(
        f"/{SLUG}/settings/api-keys", {"op": "create", "label": "CI key"}, follow=True
    )
    assert resp.status_code == 200
    # The raw key (osk_...) is revealed exactly once on the post-redirect page.
    assert b"osk_" in resp.content
    assert b"Your new API key" in resp.content

    connection.set_tenant(org)
    assert ApiKey.objects.filter(label="CI key").count() == 1
    assert AuditLog.objects.filter(action="apikey.created").exists()

    # A refresh of the page no longer shows the raw secret (one-time only).
    resp2 = client.get(f"/{SLUG}/settings/api-keys")
    assert b"Your new API key" not in resp2.content


def test_revoke_api_key(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    from apps.apikeys import services as apikey_services

    api_key, _raw = apikey_services.create_api_key(
        environment=env, label="to-revoke", created_by_user_id=owner.id
    )
    client.force_login(owner)
    client.post(
        f"/{SLUG}/settings/api-keys",
        {"op": "revoke", "key_id": str(api_key.id)},
        follow=True,
    )
    connection.set_tenant(org)
    api_key.refresh_from_db()
    assert api_key.revoked_at is not None


# --------------------------------------------------------------------------- #
#  Webhooks tab                                                               #
# --------------------------------------------------------------------------- #
def test_webhooks_page_owner_renders(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/webhooks")
    assert resp.status_code == 200
    assert b"Add an endpoint" in resp.content


def test_webhooks_page_member_403(client, org_ctx):
    org, owner, env = org_ctx
    member = _add_member(org, "ps_member2@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/settings/webhooks")
    assert resp.status_code == 403


def test_create_webhook_persists_with_events_and_audits(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/settings/webhooks",
        {
            "op": "create",
            "url": "https://example.com/hook",
            "events": [WebhookEvent.SURVEY_PUBLISHED, WebhookEvent.RESPONSE_FINISHED],
        },
        follow=True,
    )
    assert resp.status_code == 200
    # Signing secret revealed once on creation.
    assert b"Your signing secret" in resp.content

    connection.set_tenant(org)
    wh = Webhook.objects.filter(url="https://example.com/hook").first()
    assert wh is not None
    assert WebhookEvent.SURVEY_PUBLISHED in wh.events
    assert WebhookEvent.RESPONSE_FINISHED in wh.events
    assert AuditLog.objects.filter(action="webhook.created").exists()


def test_delete_webhook(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    from apps.webhooks import services as webhook_services

    wh = webhook_services.create_webhook(
        environment=env, url="https://example.com/del", events=[WebhookEvent.SURVEY_CLOSED]
    )
    client.force_login(owner)
    client.post(
        f"/{SLUG}/settings/webhooks",
        {"op": "delete", "webhook_id": str(wh.id)},
        follow=True,
    )
    connection.set_tenant(org)
    assert not Webhook.objects.filter(pk=wh.id).exists()
    assert AuditLog.objects.filter(action="webhook.deleted").exists()
