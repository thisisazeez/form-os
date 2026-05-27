"""
Focused model tests for webhooks (Webhook, contracts/models.md §2.14; Phase 7 model frozen now).

Coverage: events JSONField default + round-trip (subset of WebhookEvent), enabled default,
and the cross-schema created_by_user_id pattern.
"""
import uuid

import pytest
from django.db import connection

from apps.webhooks.enums import WebhookEvent
from apps.webhooks.models import Webhook
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace


def _make_env(demo_tenant):
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    return Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)


@pytest.mark.django_db
def test_webhook_defaults_and_events_roundtrip(demo_tenant):
    env = _make_env(demo_tenant)
    wh = Webhook.objects.create(
        environment=env, url="https://hooks.example.com/x", secret="s3cr3t"
    )
    assert wh.events == []  # default=list
    assert wh.enabled is True
    events = [WebhookEvent.RESPONSE_FINISHED.value, WebhookEvent.SURVEY_PUBLISHED.value]
    wh.events = events
    wh.save()
    wh.refresh_from_db()
    assert wh.events == events


@pytest.mark.django_db
def test_webhook_created_by_user_id_no_fk(demo_tenant):
    field = Webhook._meta.get_field("created_by_user_id")
    assert field.get_internal_type() == "UUIDField"
    assert not field.is_relation


def test_webhook_event_enum_values():
    """Lock the canonical WebhookEvent string values (models.md PART C)."""
    assert WebhookEvent.RESPONSE_CREATED == "response.created"
    assert WebhookEvent.RESPONSE_FINISHED == "response.finished"
    assert WebhookEvent.SURVEY_PUBLISHED == "survey.published"
    assert WebhookEvent.SURVEY_CLOSED == "survey.closed"
