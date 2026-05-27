"""
Follow-ups SCHEMA + in-code validation tests (no DB needed — pure validator).

Covers the WS-4 contract surface:
  - survey-schema.json accepts a well-formed `followUps` array (all three recipient forms,
    conditional + unconditional triggers, i18n subject/body, recall tokens in text).
  - malformed follow-ups are REJECTED — by jsonschema (bad discriminator / missing required
    keys / additionalProperties) AND by the in-code cross-reference checks (condition /
    responseField refs that don't resolve; duplicate follow-up ids).

These run against ``apps.surveys.validators.validate_survey_definition`` (the single write-path
entry point), exercising the snake_case ``follow_ups`` -> camelCase ``followUps`` translation
that makes the #/$defs/followUp subschema actually run.
"""
import pytest

from apps.surveys.validators import (
    SurveyValidationError,
    validate_survey_definition,
)

# A minimal valid base survey (questions + languages are the schema's top-level required keys).
_BASE = {
    "name": "FU survey",
    "languages": [{"code": "en", "label": "English", "default": True}],
    "questions": [
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
            "headline": {"default": "Email?"},
            "config": {"fields": {"email": {"show": True, "required": True}}},
        },
    ],
}


def _survey(**overrides):
    s = dict(_BASE)
    s.update(overrides)
    return s


def test_accepts_unconditional_fixed_email_followup():
    """Unconditional trigger + fixedEmail recipient + i18n subject/body validates."""
    validate_survey_definition(_survey(follow_ups=[
        {
            "id": "fu1",
            "name": "Ops ping",
            "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "ops@acme.com"},
                "subject": {"default": "New response"},
                "body": {"default": "<p>A response came in.</p>"},
            },
        }
    ]))  # must not raise


def test_accepts_conditional_contact_and_responsefield_recipients_with_recall():
    """Conditional trigger + contact recipient + responseField recipient + recall tokens."""
    validate_survey_definition(_survey(follow_ups=[
        {
            "id": "fu_contact",
            "trigger": {
                "type": "responseFinished",
                "condition": {"leftQuestionId": "interested", "operator": "equals", "value": "yes"},
            },
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "contact"},
                "subject": {"default": "Hi #recall:email/fallback:there#"},
                "body": {"default": "<p>Thanks #recall:email/fallback:friend#.</p>"},
            },
        },
        {
            "id": "fu_respondent",
            "trigger": {"type": "responseFinished", "condition": None},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "responseField", "questionId": "email"},
                "subject": {"default": "Receipt"},
                "body": {"default": "<p>We'll be in touch.</p>"},
            },
        },
    ]))  # must not raise


def test_empty_followups_is_valid():
    validate_survey_definition(_survey(follow_ups=[]))  # must not raise


@pytest.mark.parametrize(
    "bad_followup",
    [
        # Unknown trigger type (enum violation).
        {
            "id": "f", "trigger": {"type": "onView"},
            "action": {
                "type": "sendEmail", "recipient": {"type": "fixedEmail", "email": "a@b.com"},
                "subject": {"default": "s"}, "body": {"default": "b"},
            },
        },
        # Missing action.recipient (required).
        {
            "id": "f", "trigger": {"type": "responseFinished"},
            "action": {"type": "sendEmail", "subject": {"default": "s"}, "body": {"default": "b"}},
        },
        # Recipient discriminator with extra key (additionalProperties:false).
        {
            "id": "f", "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "contact", "email": "x@y.com"},
                "subject": {"default": "s"}, "body": {"default": "b"},
            },
        },
        # subject missing the required 'default' i18n key.
        {
            "id": "f", "trigger": {"type": "responseFinished"},
            "action": {
                "type": "sendEmail",
                "recipient": {"type": "fixedEmail", "email": "a@b.com"},
                "subject": {"en": "s"}, "body": {"default": "b"},
            },
        },
        # Unknown action type.
        {
            "id": "f", "trigger": {"type": "responseFinished"},
            "action": {
                "type": "createTask",
                "recipient": {"type": "fixedEmail", "email": "a@b.com"},
                "subject": {"default": "s"}, "body": {"default": "b"},
            },
        },
    ],
)
def test_rejects_schema_malformed_followup(bad_followup):
    with pytest.raises(SurveyValidationError):
        validate_survey_definition(_survey(follow_ups=[bad_followup]))


def test_rejects_unknown_condition_ref():
    """A trigger condition referencing a non-existent question id is rejected (in-code)."""
    with pytest.raises(SurveyValidationError) as exc:
        validate_survey_definition(_survey(follow_ups=[
            {
                "id": "fu1",
                "trigger": {
                    "type": "responseFinished",
                    "condition": {"leftQuestionId": "nope", "operator": "equals", "value": "x"},
                },
                "action": {
                    "type": "sendEmail",
                    "recipient": {"type": "fixedEmail", "email": "a@b.com"},
                    "subject": {"default": "s"}, "body": {"default": "b"},
                },
            }
        ]))
    codes = {e.get("code") for e in exc.value.logic_errors}
    assert "followup_unknown_condition_ref" in codes


def test_rejects_unknown_responsefield_recipient_ref():
    """A responseField recipient pointing at a non-existent question id is rejected (in-code)."""
    with pytest.raises(SurveyValidationError) as exc:
        validate_survey_definition(_survey(follow_ups=[
            {
                "id": "fu1",
                "trigger": {"type": "responseFinished"},
                "action": {
                    "type": "sendEmail",
                    "recipient": {"type": "responseField", "questionId": "ghost"},
                    "subject": {"default": "s"}, "body": {"default": "b"},
                },
            }
        ]))
    codes = {e.get("code") for e in exc.value.logic_errors}
    assert "followup_unknown_recipient_ref" in codes


def test_rejects_duplicate_followup_ids():
    fu = {
        "id": "dup",
        "trigger": {"type": "responseFinished"},
        "action": {
            "type": "sendEmail",
            "recipient": {"type": "fixedEmail", "email": "a@b.com"},
            "subject": {"default": "s"}, "body": {"default": "b"},
        },
    }
    with pytest.raises(SurveyValidationError) as exc:
        validate_survey_definition(_survey(follow_ups=[fu, dict(fu)]))
    codes = {e.get("code") for e in exc.value.logic_errors}
    assert "followup_duplicate_id" in codes


def test_rejects_invalid_fixed_email():
    """A fixedEmail recipient with a non-email string is rejected (in-code; jsonschema
    format:email is advisory and not enforced by default)."""
    with pytest.raises(SurveyValidationError) as exc:
        validate_survey_definition(_survey(follow_ups=[
            {
                "id": "fu1",
                "trigger": {"type": "responseFinished"},
                "action": {
                    "type": "sendEmail",
                    "recipient": {"type": "fixedEmail", "email": "not-an-email"},
                    "subject": {"default": "s"}, "body": {"default": "b"},
                },
            }
        ]))
    codes = {e.get("code") for e in exc.value.logic_errors}
    assert "followup_invalid_recipient_email" in codes


def test_condition_ref_to_hidden_field_is_accepted():
    """A condition may reference a survey-level hidden field id (a valid answer source)."""
    validate_survey_definition(_survey(
        hidden_fields=[{"id": "plan", "name": "Plan"}],
        follow_ups=[
            {
                "id": "fu1",
                "trigger": {
                    "type": "responseFinished",
                    "condition": {"leftQuestionId": "plan", "operator": "equals", "value": "pro"},
                },
                "action": {
                    "type": "sendEmail",
                    "recipient": {"type": "fixedEmail", "email": "a@b.com"},
                    "subject": {"default": "s"}, "body": {"default": "b"},
                },
            }
        ],
    ))  # must not raise
