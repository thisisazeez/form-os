"""
Follow-up SEND pipeline tests — finalize -> trigger eval -> recipient -> render -> enqueue.

The email TRANSPORT is the locmem backend (config.settings.ci EMAIL_BACKEND) and Celery runs
eager (CELERY_TASK_ALWAYS_EAGER), so a finalize synchronously renders + "sends" the follow-up
into ``django.core.mail.outbox`` — the real pipeline, mocked only at the transport. We assert:

  - a matching/unconditional follow-up FIRES on finalize -> outbox carries the rendered
    subject (recall-resolved) + HTML body + the right recipient; a FollowUpDelivery row is SENT.
  - a follow-up whose condition is FALSE does NOT fire (no email, no delivery row).
  - recipient resolution: fixedEmail / responseField (respondent email) / contact (attribute).
  - failures never break finalize (the response is still finished even if the send raises).
  - idempotency: re-finalize does not double-send.

Org creation provisions a Postgres schema (DDL) → transactional_db + force_drop cleanup.
"""
import uuid
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection

from apps.followups.enums import FollowUpDeliveryStatus
from apps.followups.models import FollowUpDelivery
from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.responses import public_services
from apps.responses.models import Response
from apps.surveys import services as survey_services
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.surveys.services import transition_status
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "followups-pipeline-org"

LANGUAGES = [{"code": "en", "label": "English", "default": True}]
QUESTIONS = [
    {
        "id": "interested",
        "type": "singleSelect",
        "headline": {"default": "Interested?"},
        "config": {
            "choices": [
                {"id": "yes", "label": {"default": "Yes"}},
                {"id": "no", "label": {"default": "No"}},
            ]
        },
    },
    {
        "id": "email",
        "type": "contactInfo",
        "required": False,
        "headline": {"default": "Email?"},
        "config": {"fields": {"email": {"show": True, "required": False}}},
    },
    {
        "id": "name",
        "type": "openText",
        "required": False,
        "headline": {"default": "Your name?"},
        "config": {"inputType": "short"},
    },
]


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup():
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=SLUG):
        org.delete(force_drop=True)


@pytest.fixture
def ctx(transactional_db):
    """Org + production env, tenant schema active. Yields (org, env)."""
    owner = _mk_user("followups_owner@example.com")
    _cleanup()
    org = create_organization(name="Follow-ups Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, env
    _cleanup()


def _mk_survey(env, follow_ups, **kwargs):
    defaults = dict(
        name="FU survey",
        status=SurveyStatus.PUBLISHED,
        questions=QUESTIONS,
        languages=LANGUAGES,
        endings=[{"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}}],
        follow_ups=follow_ups,
    )
    defaults.update(kwargs)
    return Survey.objects.create(environment=env, **defaults)


def _finalize(survey, data, **resp_kwargs):
    """Create a finished-pending response with ``data`` and finalize it (fires follow-ups)."""
    response = Response.objects.create(
        survey=survey, data=data, finished=False, language="en", **resp_kwargs
    )
    return public_services.finalize_response(response=response)


# --------------------------------------------------------------------------- #
#  Firing + rendering (with recall)                                            #
# --------------------------------------------------------------------------- #
def test_unconditional_followup_sends_with_recall_resolved(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "name": "Ops ping",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "ops@acme.com"},
                "subject": {"default": "Lead: #recall:name/fallback:(anon)#"},
                "body": {"default": "<p>Reach #recall:name/fallback:them# soon.</p>"},
            },
        }
    ])
    mail.outbox.clear()

    # `name` is a scalar (openText) answer — recall pipes it. (contactInfo `email` is an
    # object-shaped answer and is NOT pipeable by recall, per the frozen TS rule — that path
    # is covered by the responseField recipient test instead.)
    _finalize(survey, {"interested": "yes", "name": "Ada Lovelace"})

    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["ops@acme.com"]
    # Subject = recall-resolved + HTML-stripped (plain).
    assert msg.subject == "Lead: Ada Lovelace"
    # Body HTML alternative carries the recall-resolved body. Django's `alternatives` is a
    # list of (content, mimetype) tuples; pick the text/html one.
    html = next((c for (c, t) in msg.alternatives if t == "text/html"), "")
    assert "Ada Lovelace" in html and "<p>" in html

    delivery = FollowUpDelivery.objects.get(follow_up_id="fu1")
    assert delivery.status == FollowUpDeliveryStatus.SENT
    assert delivery.recipient_email == "ops@acme.com"
    assert delivery.rendered_subject == "Lead: Ada Lovelace"
    assert delivery.attempts == 1


def test_recall_fallback_when_source_unanswered(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "ops@acme.com"},
                "subject": {"default": "Lead #recall:name/fallback:(unknown)#"},
                "body": {"default": "<p>hi</p>"},
            },
        }
    ])
    mail.outbox.clear()

    _finalize(survey, {"interested": "yes"})  # `name` source unanswered -> fallback

    assert mail.outbox[0].subject == "Lead (unknown)"


# --------------------------------------------------------------------------- #
#  Condition gating                                                            #
# --------------------------------------------------------------------------- #
def test_condition_true_fires(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {
                "type": "responseFinished",
                "condition": {"leftQuestionId": "interested", "operator": "equals", "value": "yes"},
            },
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "sales@acme.com"},
                "subject": {"default": "Interested lead"},
                "body": {"default": "<p>go</p>"},
            },
        }
    ])
    mail.outbox.clear()

    _finalize(survey, {"interested": "yes"})

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["sales@acme.com"]


def test_condition_false_does_not_fire(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {
                "type": "responseFinished",
                "condition": {"leftQuestionId": "interested", "operator": "equals", "value": "yes"},
            },
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "sales@acme.com"},
                "subject": {"default": "Interested lead"},
                "body": {"default": "<p>go</p>"},
            },
        }
    ])
    mail.outbox.clear()

    _finalize(survey, {"interested": "no"})  # condition (== yes) is FALSE

    assert len(mail.outbox) == 0
    assert FollowUpDelivery.objects.count() == 0


# --------------------------------------------------------------------------- #
#  Recipient resolution                                                        #
# --------------------------------------------------------------------------- #
def test_responsefield_recipient_uses_respondent_email(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "responseField", "questionId": "email"},
                "subject": {"default": "Receipt"},
                "body": {"default": "<p>thanks</p>"},
            },
        }
    ])
    mail.outbox.clear()

    _finalize(survey, {"interested": "yes", "email": {"email": "respondent@x.com"}})

    assert mail.outbox[0].to == ["respondent@x.com"]


def test_responsefield_recipient_blank_email_records_skipped(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "responseField", "questionId": "email"},
                "subject": {"default": "Receipt"},
                "body": {"default": "<p>thanks</p>"},
            },
        }
    ])
    mail.outbox.clear()

    _finalize(survey, {"interested": "yes"})  # no email answer -> no recipient

    assert len(mail.outbox) == 0
    delivery = FollowUpDelivery.objects.get(follow_up_id="fu1")
    assert delivery.status == FollowUpDeliveryStatus.SKIPPED
    assert delivery.recipient_email == ""


def test_contact_recipient_uses_contact_email_attribute(ctx):
    org, env = ctx
    connection.set_tenant(org)
    from apps.contacts.models import Contact, ContactAttribute, ContactAttributeKey

    contact = Contact.objects.create(environment=env)
    email_key = ContactAttributeKey.objects.create(environment=env, key="email")
    ContactAttribute.objects.create(
        contact=contact, attribute_key=email_key, value="contact@x.com"
    )

    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "contact"},
                "subject": {"default": "Hi"},
                "body": {"default": "<p>hi</p>"},
            },
        }
    ])
    mail.outbox.clear()

    _finalize(survey, {"interested": "yes"}, contact_id=contact.id)

    assert mail.outbox[0].to == ["contact@x.com"]


def test_contact_recipient_anonymous_response_skips(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "contact"},
                "subject": {"default": "Hi"},
                "body": {"default": "<p>hi</p>"},
            },
        }
    ])
    mail.outbox.clear()

    _finalize(survey, {"interested": "yes"})  # anonymous (no contact_id)

    assert len(mail.outbox) == 0
    assert FollowUpDelivery.objects.get(follow_up_id="fu1").status == (
        FollowUpDeliveryStatus.SKIPPED
    )


# --------------------------------------------------------------------------- #
#  Robustness                                                                  #
# --------------------------------------------------------------------------- #
def test_no_followups_no_send(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[])
    mail.outbox.clear()
    resp = _finalize(survey, {"interested": "yes"})
    assert resp.finished is True
    assert len(mail.outbox) == 0


def test_send_failure_does_not_break_finalize(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "ops@acme.com"},
                "subject": {"default": "S"},
                "body": {"default": "<p>b</p>"},
            },
        }
    ])
    mail.outbox.clear()

    # Make the underlying send raise; finalize must still succeed and record FAILED.
    with mock.patch(
        "apps.accounts.tasks.send_mail_task", side_effect=RuntimeError("smtp down")
    ):
        resp = _finalize(survey, {"interested": "yes"})

    assert resp.finished is True  # finalize unaffected
    delivery = FollowUpDelivery.objects.get(follow_up_id="fu1")
    assert delivery.status == FollowUpDeliveryStatus.FAILED
    assert "smtp down" in delivery.error


def test_refinalize_is_idempotent_no_double_send(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "fu1",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "ops@acme.com"},
                "subject": {"default": "S"},
                "body": {"default": "<p>b</p>"},
            },
        }
    ])
    mail.outbox.clear()

    response = Response.objects.create(
        survey=survey, data={"interested": "yes"}, finished=False, language="en"
    )
    public_services.finalize_response(response=response)
    # Force a second dispatch pass (mirrors a re-fire); the unique constraint must dedup.
    from apps.followups.services import dispatch_follow_ups

    dispatch_follow_ups(response)

    assert FollowUpDelivery.objects.filter(follow_up_id="fu1").count() == 1
    assert len(mail.outbox) == 1


def test_multiple_followups_each_evaluated(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, follow_ups=[
        {
            "id": "always",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "ops@acme.com"},
                "subject": {"default": "Always"},
                "body": {"default": "<p>a</p>"},
            },
        },
        {
            "id": "only-yes",
            "trigger": {
                "type": "responseFinished",
                "condition": {"leftQuestionId": "interested", "operator": "equals", "value": "yes"},
            },
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "sales@acme.com"},
                "subject": {"default": "Yes lead"},
                "body": {"default": "<p>y</p>"},
            },
        },
    ])
    mail.outbox.clear()

    _finalize(survey, {"interested": "no"})  # only the unconditional one fires

    recipients = sorted(r for m in mail.outbox for r in m.to)
    assert recipients == ["ops@acme.com"]
    assert FollowUpDelivery.objects.filter(follow_up_id="always").exists()
    assert not FollowUpDelivery.objects.filter(follow_up_id="only-yes").exists()


# --------------------------------------------------------------------------- #
#  End-to-end via the survey service (create -> validate -> publish -> finalize) #
# --------------------------------------------------------------------------- #
def test_full_path_via_create_survey_service(ctx):
    """Belt-and-braces: a follow-up authored through create_survey (which runs the full
    validate_survey_definition) survives persistence and fires on finalize."""
    org, env = ctx
    connection.set_tenant(org)
    survey = survey_services.create_survey(
        environment=env,
        name="E2E FU",
        questions=QUESTIONS,
        languages=LANGUAGES,
        endings=[{"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}}],
        follow_ups=[
            {
                "id": "fu1",
                "name": "Sales ping",
                "trigger": {
                    "type": "responseFinished",
                    "condition": {
                        "leftQuestionId": "interested",
                        "operator": "equals",
                        "value": "yes",
                    },
                },
                "action": {
                    "type": "sendEmail",
                    "recipient": {"type": "fixedEmail", "email": "sales@acme.com"},
                    "subject": {"default": "Lead #recall:name/fallback:(anon)#"},
                    "body": {"default": "<p>New interested lead.</p>"},
                },
            }
        ],
    )
    # Publish it (the lifecycle the runtime requires before responses come in).
    survey = transition_status(survey=survey, target_status=SurveyStatus.PUBLISHED)
    assert survey.follow_ups[0]["id"] == "fu1"  # persisted on the column

    mail.outbox.clear()
    _finalize(survey, {"interested": "yes", "name": "Ada"})

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["sales@acme.com"]
    assert mail.outbox[0].subject == "Lead Ada"
    assert FollowUpDelivery.objects.get(follow_up_id="fu1").status == (
        FollowUpDeliveryStatus.SENT
    )
