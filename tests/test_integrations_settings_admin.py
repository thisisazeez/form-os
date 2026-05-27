"""
Management-UI tests for the Integrations Settings hub (server-rendered HTML).

Covers: owner can open the hub (200) + connect a Slack integration (persists with events +
survey scope + audit), update it, disconnect it; a plain member is 403; the hub surfaces the
Webhooks tab by link (not duplicated) and lists the deferred integrations read-only with NO
connect buttons.

Django session Client + force_login (these are @login_required HTML views, not DRF). Org
creation provisions a schema (DDL) → transactional_db + force_drop (conftest.py). A workspace +
production Environment exist (auto-provisioned by create_organization) so Slack has a target env.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.audit.models import AuditLog
from apps.integrations.models import SlackIntegration
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.webhooks.enums import WebhookEvent
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment

User = get_user_model()

SLUG = "integrations-settings-org"
SLACK_URL = "https://hooks.slack.com/services/T0/B0/secret"


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
    owner = _mk_user("int_owner@example.com")
    _cleanup()
    org = create_organization(name="Int Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
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
#  Page render + RBAC                                                          #
# --------------------------------------------------------------------------- #
def test_integrations_hub_owner_renders(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/integrations")
    assert resp.status_code == 200
    assert b"Connect a Slack channel" in resp.content
    # Webhooks surfaced by LINK (hub, not duplicate) + deferred section present.
    assert f"/{SLUG}/settings/webhooks".encode() in resp.content
    assert b"More integrations" in resp.content
    assert b"Google Sheets" in resp.content


def test_integrations_hub_member_403(client, org_ctx):
    org, owner, env = org_ctx
    member = _add_member(org, "int_member@example.com")
    client.force_login(member)
    resp = client.get(f"/{SLUG}/settings/integrations")
    assert resp.status_code == 403


def test_deferred_integrations_have_no_connect_buttons(client, org_ctx):
    """The deferred integrations must be read-only — descriptive markup, no form/button.

    The deferred section ("More integrations") is rendered last. We slice the body from that
    heading to the end and assert it contains NO ``<form`` and NO submit button — proving the
    deferred cards (Google Sheets, Airtable, Notion, Zapier/Make/n8n) carry no dead "Connect"
    controls. The Webhooks card reaches its page by anchor (link), not a button.
    """
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/integrations")
    body = resp.content.decode()
    assert "slack_connect" in body  # the one functional connect form exists

    idx = body.index("More integrations")
    deferred_html = body[idx:]
    assert "<form" not in deferred_html
    assert 'type="submit"' not in deferred_html
    # And the Webhooks card reaches its page by anchor, not a button/form.
    assert f'href="/{SLUG}/settings/webhooks"' in body


# --------------------------------------------------------------------------- #
#  Slack connect / update / disconnect                                        #
# --------------------------------------------------------------------------- #
def test_connect_slack_persists_with_events_scope_and_audits(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.post(
        f"/{SLUG}/settings/integrations",
        {
            "op": "slack_connect",
            "incoming_webhook_url": SLACK_URL,
            "name": "#feedback",
            "events": [WebhookEvent.SURVEY_PUBLISHED, WebhookEvent.RESPONSE_FINISHED],
        },
        follow=True,
    )
    assert resp.status_code == 200

    connection.set_tenant(org)
    si = SlackIntegration.objects.filter(incoming_webhook_url=SLACK_URL).first()
    assert si is not None
    assert si.name == "#feedback"
    assert WebhookEvent.SURVEY_PUBLISHED in si.events
    assert WebhookEvent.RESPONSE_FINISHED in si.events
    assert si.survey_ids == []  # none selected == all surveys
    assert AuditLog.objects.filter(action="integration.slack.connected").exists()


def test_connect_slack_member_forbidden(client, org_ctx):
    org, owner, env = org_ctx
    member = _add_member(org, "int_member2@example.com")
    client.force_login(member)
    client.post(
        f"/{SLUG}/settings/integrations",
        {"op": "slack_connect", "incoming_webhook_url": SLACK_URL, "events": [WebhookEvent.SURVEY_PUBLISHED]},
    )
    connection.set_tenant(org)
    assert not SlackIntegration.objects.filter(incoming_webhook_url=SLACK_URL).exists()


def test_update_slack_integration(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    from apps.integrations import services

    si = services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.SURVEY_PUBLISHED], name="old",
    )
    client.force_login(owner)
    client.post(
        f"/{SLUG}/settings/integrations",
        {
            "op": "slack_update",
            "integration_id": str(si.id),
            "incoming_webhook_url": SLACK_URL,
            "name": "new",
            "events": [WebhookEvent.SURVEY_CLOSED],
            "enabled": "on",
        },
        follow=True,
    )
    connection.set_tenant(org)
    si.refresh_from_db()
    assert si.name == "new"
    assert si.events == [WebhookEvent.SURVEY_CLOSED]
    assert AuditLog.objects.filter(action="integration.slack.updated").exists()


def test_disconnect_slack_integration(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    from apps.integrations import services

    si = services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.SURVEY_CLOSED],
    )
    client.force_login(owner)
    client.post(
        f"/{SLUG}/settings/integrations",
        {"op": "slack_disconnect", "integration_id": str(si.id)},
        follow=True,
    )
    connection.set_tenant(org)
    assert not SlackIntegration.objects.filter(pk=si.id).exists()
    assert AuditLog.objects.filter(action="integration.slack.disconnected").exists()
