"""
Webhook delivery tests — firing on events, with the HTTP call MOCKED.

CELERY_TASK_ALWAYS_EAGER (config.settings.ci) runs the delivery task inline, so dispatching an
event synchronously invokes the task; we mock the HTTP boundary (apps.webhooks.tasks._http_post)
and assert it was called with a signed payload, that only SUBSCRIBED + ENABLED endpoints fire,
and that failures are recorded on the Webhook row.

Org creation provisions a Postgres schema (DDL) → transactional_db + force_drop (conftest.py).
"""
import json
import urllib.error
import uuid
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.surveys import services as survey_services
from apps.surveys.enums import SurveyStatus
from apps.webhooks import services as webhook_services
from apps.webhooks.enums import WebhookEvent
from apps.webhooks.tasks import SIGNATURE_HEADER, sign_payload
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "webhook-delivery-org"


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup():
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=SLUG):
        org.delete(force_drop=True)


@pytest.fixture
def env_ctx(transactional_db):
    """Org + a production Environment, tenant schema active. Yields (org, owner, env)."""
    owner = _mk_user("webhook_owner@example.com")
    _cleanup()
    org = create_organization(name="Webhook Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
    _cleanup()


def _draft_survey(env):
    """A schema-valid draft survey (publishing it is a legal draft → published transition)."""
    return survey_services.create_survey(
        environment=env,
        name="Webhook survey",
        questions=[
            {
                "id": "q1",
                "type": "openText",
                "headline": {"default": "How was it?"},
                "config": {"inputType": "long"},
            }
        ],
        languages=[{"code": "en", "label": "English", "default": True}],
        endings=[{"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}}],
    )


def test_dispatch_fires_only_subscribed_enabled_webhooks(env_ctx):
    org, owner, env = env_ctx
    connection.set_tenant(org)

    # subscribed + enabled → fires
    webhook_services.create_webhook(
        environment=env, url="https://example.com/hook-a", events=[WebhookEvent.SURVEY_PUBLISHED]
    )
    # subscribed to a DIFFERENT event → must NOT fire
    webhook_services.create_webhook(
        environment=env, url="https://example.com/hook-b", events=[WebhookEvent.RESPONSE_FINISHED]
    )
    # subscribed but DISABLED → must NOT fire
    webhook_services.create_webhook(
        environment=env, url="https://example.com/hook-c",
        events=[WebhookEvent.SURVEY_PUBLISHED], enabled=False,
    )

    with mock.patch("apps.webhooks.tasks._http_post", return_value=200) as http:
        count = webhook_services.dispatch_event(
            event=WebhookEvent.SURVEY_PUBLISHED, payload={"survey_id": "x"}
        )

    assert count == 1  # only hook-a is enabled + subscribed
    assert http.call_count == 1
    called_url = http.call_args.args[0]
    assert called_url == "https://example.com/hook-a"


def test_delivery_sends_signed_payload(env_ctx):
    org, owner, env = env_ctx
    connection.set_tenant(org)
    webhook = webhook_services.create_webhook(
        environment=env, url="https://example.com/hook", events=[WebhookEvent.SURVEY_PUBLISHED]
    )

    with mock.patch("apps.webhooks.tasks._http_post", return_value=200) as http:
        webhook_services.dispatch_event(
            event=WebhookEvent.SURVEY_PUBLISHED, payload={"survey_id": "abc"}
        )

    assert http.call_count == 1
    url, body, headers = http.call_args.args
    # Body is the signed envelope; signature header matches HMAC of the raw body.
    envelope = json.loads(body)
    assert envelope["event"] == WebhookEvent.SURVEY_PUBLISHED
    assert envelope["data"] == {"survey_id": "abc"}
    assert headers[SIGNATURE_HEADER] == sign_payload(webhook.secret, body)


def test_survey_publish_triggers_delivery_end_to_end(env_ctx):
    """Publishing a survey through the service layer fires the survey.published webhook."""
    org, owner, env = env_ctx
    connection.set_tenant(org)
    webhook_services.create_webhook(
        environment=env, url="https://example.com/published", events=[WebhookEvent.SURVEY_PUBLISHED]
    )
    survey = _draft_survey(env)

    with mock.patch("apps.webhooks.tasks._http_post", return_value=200) as http:
        survey_services.transition_status(survey=survey, target_status=SurveyStatus.PUBLISHED)

    assert http.call_count == 1
    envelope = json.loads(http.call_args.args[1])
    assert envelope["event"] == WebhookEvent.SURVEY_PUBLISHED
    assert envelope["data"]["survey_id"] == str(survey.id)


def test_response_created_and_finished_trigger_delivery(env_ctx):
    """Creating then finalizing a response fires response.created then response.finished."""
    from apps.responses import public_services as response_services

    org, owner, env = env_ctx
    connection.set_tenant(org)
    webhook_services.create_webhook(
        environment=env,
        url="https://example.com/responses",
        events=[WebhookEvent.RESPONSE_CREATED, WebhookEvent.RESPONSE_FINISHED],
    )
    survey = _draft_survey(env)
    survey_services.transition_status(survey=survey, target_status=SurveyStatus.PUBLISHED)

    with mock.patch("apps.webhooks.tasks._http_post", return_value=200) as http:
        response = response_services.create_response(
            survey=survey, data={}, language="en", link=None, meta={}
        )
        response_services.finalize_response(response=response)

    fired_events = [json.loads(c.args[1])["event"] for c in http.call_args_list]
    assert WebhookEvent.RESPONSE_CREATED in fired_events
    assert WebhookEvent.RESPONSE_FINISHED in fired_events


def test_failure_is_recorded_on_webhook(env_ctx):
    """A permanent (4xx) delivery failure records last_status_code + bumps consecutive_failures."""
    org, owner, env = env_ctx
    connection.set_tenant(org)
    webhook = webhook_services.create_webhook(
        environment=env, url="https://example.com/bad", events=[WebhookEvent.SURVEY_PUBLISHED]
    )

    http_error = urllib.error.HTTPError(
        url="https://example.com/bad", code=400, msg="Bad Request", hdrs=None, fp=None
    )
    with mock.patch("apps.webhooks.tasks._http_post", side_effect=http_error):
        webhook_services.dispatch_event(
            event=WebhookEvent.SURVEY_PUBLISHED, payload={"survey_id": "x"}
        )

    connection.set_tenant(org)
    webhook.refresh_from_db()
    assert webhook.last_status_code == 400
    assert webhook.consecutive_failures == 1
    assert webhook.last_delivery_at is not None
