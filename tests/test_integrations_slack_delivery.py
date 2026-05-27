"""
Slack integration delivery tests — firing on events, with the HTTP call MOCKED.

CELERY_TASK_ALWAYS_EAGER (config.settings.ci / dev) runs the delivery task inline, so dispatching
an event synchronously invokes the task; we mock the HTTP boundary
(apps.integrations.tasks._http_post) and assert:
  - the POST goes to the integration's incoming_webhook_url with a Slack message body
    (top-level ``text`` + Block-Kit ``blocks``),
  - only SUBSCRIBED + ENABLED integrations fire,
  - survey-scoping is honored (configured for survey A does NOT fire for survey B),
  - delivery happens off the EXISTING webhook dispatcher (publishing a survey fires Slack),
  - a delivery failure is recorded AND never breaks the triggering business action (isolation).

Org creation provisions a Postgres schema (DDL) → transactional_db + force_drop (conftest.py).
"""
import json
import urllib.error
import uuid
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from apps.integrations import services as integration_services
from apps.integrations.models import SlackIntegration
from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.surveys import services as survey_services
from apps.surveys.enums import SurveyStatus
from apps.webhooks.enums import WebhookEvent
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "slack-delivery-org"
SLACK_URL = "https://hooks.slack.com/services/T000/B000/abc123"


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
    owner = _mk_user("slack_owner@example.com")
    _cleanup()
    org = create_organization(name="Slack Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
    _cleanup()


def _draft_survey(env, name="Slack survey"):
    """A schema-valid draft survey (publishing it is a legal draft → published transition)."""
    return survey_services.create_survey(
        environment=env,
        name=name,
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


def test_dispatch_fires_only_subscribed_enabled_integrations(env_ctx):
    org, owner, env = env_ctx
    connection.set_tenant(org)

    # subscribed + enabled → fires
    integration_services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.SURVEY_PUBLISHED], name="A",
    )
    # subscribed to a DIFFERENT event → must NOT fire
    integration_services.create_slack_integration(
        environment=env, incoming_webhook_url="https://hooks.slack.com/services/x/y/b",
        events=[WebhookEvent.RESPONSE_FINISHED], name="B",
    )
    # subscribed but DISABLED → must NOT fire
    integration_services.create_slack_integration(
        environment=env, incoming_webhook_url="https://hooks.slack.com/services/x/y/c",
        events=[WebhookEvent.SURVEY_PUBLISHED], enabled=False, name="C",
    )

    with mock.patch("apps.integrations.tasks._http_post", return_value=200) as http:
        count = integration_services.dispatch_event(
            event=WebhookEvent.SURVEY_PUBLISHED, payload={"survey_id": "x", "name": "S"}
        )

    assert count == 1  # only A is enabled + subscribed
    assert http.call_count == 1
    assert http.call_args.args[0] == SLACK_URL


def test_delivery_sends_slack_block_kit_body(env_ctx):
    org, owner, env = env_ctx
    connection.set_tenant(org)
    integration_services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.SURVEY_PUBLISHED],
    )

    with mock.patch("apps.integrations.tasks._http_post", return_value=200) as http:
        integration_services.dispatch_event(
            event=WebhookEvent.SURVEY_PUBLISHED,
            payload={"survey_id": "abc", "name": "Customer NPS", "status": "published"},
        )

    assert http.call_count == 1
    url, body, headers = http.call_args.args
    assert url == SLACK_URL
    assert headers["Content-Type"] == "application/json"
    message = json.loads(body)
    # Slack message contract: a fallback `text` + a Block-Kit `blocks` array.
    assert "text" in message and message["text"]
    assert isinstance(message["blocks"], list) and message["blocks"]
    # The survey name + a friendly headline show up somewhere in the rendered blocks.
    rendered = json.dumps(message)
    assert "Customer NPS" in rendered
    assert "published" in rendered.lower()


def test_survey_scoping_only_fires_for_listed_survey(env_ctx):
    """An integration scoped to survey A must NOT fire for an event about survey B."""
    org, owner, env = env_ctx
    connection.set_tenant(org)
    survey_a = _draft_survey(env, name="Survey A")
    survey_b = _draft_survey(env, name="Survey B")

    integration_services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.SURVEY_PUBLISHED], survey_ids=[str(survey_a.id)],
    )

    # Event for survey B → no POST (out of scope).
    with mock.patch("apps.integrations.tasks._http_post", return_value=200) as http_b:
        integration_services.dispatch_event(
            event=WebhookEvent.SURVEY_PUBLISHED,
            payload={"survey_id": str(survey_b.id), "name": "Survey B"},
        )
    assert http_b.call_count == 0

    # Event for survey A → fires.
    with mock.patch("apps.integrations.tasks._http_post", return_value=200) as http_a:
        integration_services.dispatch_event(
            event=WebhookEvent.SURVEY_PUBLISHED,
            payload={"survey_id": str(survey_a.id), "name": "Survey A"},
        )
    assert http_a.call_count == 1


def test_empty_survey_ids_fires_for_all_surveys(env_ctx):
    """Empty survey_ids == subscribe to every survey in the environment."""
    org, owner, env = env_ctx
    connection.set_tenant(org)
    integration_services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.SURVEY_PUBLISHED], survey_ids=[],
    )
    with mock.patch("apps.integrations.tasks._http_post", return_value=200) as http:
        integration_services.dispatch_event(
            event=WebhookEvent.SURVEY_PUBLISHED,
            payload={"survey_id": str(uuid.uuid4()), "name": "Any survey"},
        )
    assert http.call_count == 1


def test_survey_publish_fires_slack_through_existing_webhook_dispatcher(env_ctx):
    """Publishing a survey via the service layer fires Slack — through the EXISTING webhook hub.

    We mock ONLY the Slack HTTP boundary (not the webhook one) and assert Slack got the message,
    proving the integrations fan-out hangs off apps.webhooks.services.dispatch_event with no new
    call site in surveys/responses.
    """
    org, owner, env = env_ctx
    connection.set_tenant(org)
    integration_services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.SURVEY_PUBLISHED],
    )
    survey = _draft_survey(env)

    with mock.patch("apps.integrations.tasks._http_post", return_value=200) as slack_http, \
         mock.patch("apps.webhooks.tasks._http_post", return_value=200):
        survey_services.transition_status(survey=survey, target_status=SurveyStatus.PUBLISHED)

    assert slack_http.call_count == 1
    message = json.loads(slack_http.call_args.args[1])
    assert survey.name in json.dumps(message)


def test_response_events_fire_slack_through_dispatcher(env_ctx):
    """Creating then finalizing a response fires Slack for response.created + response.finished."""
    from apps.responses import public_services as response_services

    org, owner, env = env_ctx
    connection.set_tenant(org)
    integration_services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.RESPONSE_CREATED, WebhookEvent.RESPONSE_FINISHED],
    )
    survey = _draft_survey(env)
    survey_services.transition_status(survey=survey, target_status=SurveyStatus.PUBLISHED)

    with mock.patch("apps.integrations.tasks._http_post", return_value=200) as http:
        response = response_services.create_response(
            survey=survey, data={}, language="en", link=None, meta={}
        )
        response_services.finalize_response(response=response)

    assert http.call_count == 2  # created + finished


def test_failure_is_recorded_and_isolated(env_ctx):
    """A delivery failure records on the row AND never breaks the triggering business action."""
    org, owner, env = env_ctx
    connection.set_tenant(org)
    integration = integration_services.create_slack_integration(
        environment=env, incoming_webhook_url=SLACK_URL,
        events=[WebhookEvent.SURVEY_PUBLISHED],
    )
    survey = _draft_survey(env)

    http_error = urllib.error.HTTPError(
        url=SLACK_URL, code=404, msg="Not Found", hdrs=None, fp=None
    )
    # Slack POST blows up; the webhook POST is also mocked away. Publishing must STILL succeed.
    with mock.patch("apps.integrations.tasks._http_post", side_effect=http_error), \
         mock.patch("apps.webhooks.tasks._http_post", return_value=200):
        survey = survey_services.transition_status(
            survey=survey, target_status=SurveyStatus.PUBLISHED
        )

    # Business action succeeded despite the Slack failure (isolation).
    connection.set_tenant(org)
    survey.refresh_from_db()
    assert survey.status == SurveyStatus.PUBLISHED

    # Failure recorded on the integration row.
    integration.refresh_from_db()
    assert integration.last_status_code == 404
    assert integration.consecutive_failures == 1
    assert integration.last_delivery_at is not None


def test_clean_survey_ids_normalizes_to_strings():
    """survey_ids are stored as de-duplicated strings — load-bearing for the edit-form checkbox
    comparison (`s.id|stringformat:"s" in si.survey_ids`) and the dispatch scope filter."""
    cleaned = integration_services._clean_survey_ids(
        [uuid.UUID("11111111-1111-1111-1111-111111111111"), "abc", "abc", "", None]
    )
    assert cleaned == ["11111111-1111-1111-1111-111111111111", "abc"]
    assert all(isinstance(s, str) for s in cleaned)


def test_clean_events_keeps_only_known_values():
    """events are filtered to the frozen WebhookEvent value set, de-duplicated."""
    cleaned = integration_services._clean_events(
        [WebhookEvent.SURVEY_PUBLISHED, "not.an.event", WebhookEvent.SURVEY_PUBLISHED]
    )
    assert cleaned == [WebhookEvent.SURVEY_PUBLISHED]


def test_dispatch_swallows_internal_errors(env_ctx):
    """Even if the integrations dispatch path itself errors, it never raises into the caller."""
    org, owner, env = env_ctx
    connection.set_tenant(org)
    # No integrations + a broken queryset path → still returns 0, never raises.
    with mock.patch.object(
        SlackIntegration.objects, "filter", side_effect=RuntimeError("boom")
    ):
        assert (
            integration_services.dispatch_event(
                event=WebhookEvent.SURVEY_PUBLISHED, payload={"survey_id": "x"}
            )
            == 0
        )
