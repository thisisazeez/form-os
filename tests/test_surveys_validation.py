"""
Unit tests for the survey write-path validator (apps/surveys/validators.py).

Covers the two validation layers (no DB needed — pure functions):
  1. jsonschema against contracts/survey-schema.json, INCLUDING the load-bearing snake_case
     -> camelCase translation (welcome_card -> welcomeCard, close_on -> closeConditions). The
     top-level schema is additionalProperties:true, so a snake_case key would validate
     VACUOUSLY; the translation makes the welcomeCard/closeConditions subschemas actually run.
  2. the in-code validate_survey checks JSON Schema can't express: unique ids, no backward/
     unknown jump, operator/type validity, condition-ref position.
"""
from __future__ import annotations

import pytest

from apps.surveys.logic import validate_survey
from apps.surveys.validators import (
    SurveyValidationError,
    validate_against_schema,
    validate_survey_definition,
)


def _base(**overrides):
    """A minimal VALID survey definition (snake_case, model/openapi field names)."""
    payload = {
        "name": "S",
        "questions": [
            {"id": "q1", "type": "openText", "headline": {"default": "Hi"}, "config": {}}
        ],
        "logic": [],
        "languages": [{"code": "en", "label": "English", "default": True}],
        "styling": {},
        "welcome_card": None,
        "endings": [],
        "close_on": None,
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
#  jsonschema layer + the camelCase translation                               #
# --------------------------------------------------------------------------- #
def test_minimal_valid_survey_passes():
    assert validate_against_schema(_base()) == []
    validate_survey_definition(_base())  # does not raise


def test_missing_required_languages_fails_schema():
    payload = _base()
    del payload["languages"]
    errors = validate_against_schema(payload)
    assert errors, "schema must require 'languages'"


def test_bad_question_type_fails_schema():
    payload = _base(
        questions=[{"id": "q1", "type": "notARealType", "headline": {"default": "Hi"}}]
    )
    assert validate_against_schema(payload), "unknown question type must fail the schema"


def test_i18n_string_missing_default_fails_schema():
    # headline is an i18nString and MUST carry the 'default' key.
    payload = _base(
        questions=[{"id": "q1", "type": "openText", "headline": {"en": "Hi"}, "config": {}}]
    )
    assert validate_against_schema(payload), "i18nString without 'default' must fail"


def test_welcome_card_translated_and_validated():
    """The KEY trick: a MALFORMED welcome_card must be REJECTED. It is only caught because the
    validator maps welcome_card -> welcomeCard (additionalProperties:false) before jsonschema —
    under the raw snake_case key the top-level additionalProperties:true would pass it vacuously.
    """
    # 'enabled' must be a boolean; 'bogus' is an unexpected property in welcomeCard.
    bad = {"enabled": "yes", "bogus": 1, "headline": {"default": "W"}}
    errors = validate_against_schema(_base(welcome_card=bad))
    assert errors, "malformed welcome_card must be rejected via the camelCase translation"
    # And a well-formed welcome_card passes.
    good = {"enabled": True, "headline": {"default": "Welcome"}}
    assert validate_against_schema(_base(welcome_card=good)) == []


def test_close_on_translated_and_validated():
    """close_on -> closeConditions translation: a bad key in close_on is caught."""
    bad = {"by_response_cap": 0}  # minimum is 1
    assert validate_against_schema(_base(close_on=bad)), "by_response_cap < 1 must fail"
    good = {"by_response_cap": 100}
    assert validate_against_schema(_base(close_on=good)) == []


def test_null_optional_keys_pass():
    """welcome_card=None / close_on=None mean 'absent' and must validate."""
    assert validate_against_schema(_base(welcome_card=None, close_on=None)) == []


# --------------------------------------------------------------------------- #
#  parity question types: pictureChoice / address / scheduleMeeting           #
#  Each: (a) a valid survey passes BOTH layers; (b) a malformed per-type       #
#  config is REJECTED — proving the per-type subschema actually fired (not     #
#  vacuously passed through the open question object).                         #
# --------------------------------------------------------------------------- #
def test_picture_choice_authorable_and_validated():
    good = _base(
        questions=[
            {
                "id": "p1",
                "type": "pictureChoice",
                "headline": {"default": "Pick an image"},
                "config": {
                    "allowMulti": True,
                    "options": [
                        {"id": "o1", "imageUrl": "https://x/1.png", "label": {"default": "One"}},
                        {"id": "o2", "imageUrl": "https://x/2.png"},
                    ],
                },
            }
        ]
    )
    assert validate_against_schema(good) == []
    validate_survey_definition(good)  # both layers; does not raise

    # < 2 options must fail the per-type minItems.
    bad_count = _base(
        questions=[
            {
                "id": "p1",
                "type": "pictureChoice",
                "headline": {"default": "Pick"},
                "config": {"options": [{"id": "o1", "imageUrl": "https://x/1.png"}]},
            }
        ]
    )
    assert validate_against_schema(bad_count), "pictureChoice with < 2 options must be rejected"

    # An option missing the required imageUrl must fail.
    bad_opt = _base(
        questions=[
            {
                "id": "p1",
                "type": "pictureChoice",
                "headline": {"default": "Pick"},
                "config": {"options": [{"id": "o1"}, {"id": "o2", "imageUrl": "https://x/2.png"}]},
            }
        ]
    )
    assert validate_against_schema(bad_opt), "pictureChoice option without imageUrl must be rejected"


def test_address_authorable_and_validated():
    good = _base(
        questions=[
            {
                "id": "a1",
                "type": "address",
                "headline": {"default": "Your address"},
                "config": {
                    "fields": {
                        "addressLine1": {"show": True, "required": True},
                        "city": {"show": True},
                        "zip": {"show": True},
                        "country": {"show": True},
                    }
                },
            }
        ]
    )
    assert validate_against_schema(good) == []
    validate_survey_definition(good)  # both layers; does not raise

    # Empty fields object must fail (minProperties: 1).
    bad_empty = _base(
        questions=[
            {
                "id": "a1",
                "type": "address",
                "headline": {"default": "Addr"},
                "config": {"fields": {}},
            }
        ]
    )
    assert validate_against_schema(bad_empty), "address with no shown fields must be rejected"

    # An unknown sub-field must fail (additionalProperties: false).
    bad_field = _base(
        questions=[
            {
                "id": "a1",
                "type": "address",
                "headline": {"default": "Addr"},
                "config": {"fields": {"province": {"show": True}}},
            }
        ]
    )
    assert validate_against_schema(bad_field), "address with an unknown sub-field must be rejected"


def test_schedule_meeting_authorable_and_validated():
    good = _base(
        questions=[
            {
                "id": "s1",
                "type": "scheduleMeeting",
                "headline": {"default": "Book a time"},
                "config": {"calLink": "jane/30min"},
            }
        ]
    )
    assert validate_against_schema(good) == []
    validate_survey_definition(good)  # both layers; does not raise

    # Missing calLink must fail (required).
    bad_missing = _base(
        questions=[
            {
                "id": "s1",
                "type": "scheduleMeeting",
                "headline": {"default": "Meet"},
                "config": {},
            }
        ]
    )
    assert validate_against_schema(bad_missing), "scheduleMeeting without calLink must be rejected"

    # Empty calLink must fail (minLength: 1).
    bad_blank = _base(
        questions=[
            {
                "id": "s1",
                "type": "scheduleMeeting",
                "headline": {"default": "Meet"},
                "config": {"calLink": ""},
            }
        ]
    )
    assert validate_against_schema(bad_blank), "scheduleMeeting with blank calLink must be rejected"


def test_logic_ops_valid_for_new_types():
    """The logic layer must accept operators valid for the new types (address ->
    text ops; scheduleMeeting -> isSubmitted/isSkipped) and reject invalid ones."""
    # address with a text operator (contains) used in a downstream jump — valid.
    ok = _base(
        questions=[
            {
                "id": "a1",
                "type": "address",
                "headline": {"default": "Addr"},
                "config": {"fields": {"city": {"show": True}}},
                "logic": [
                    {
                        "id": "r1",
                        "condition": {"leftQuestionId": "a1", "operator": "isSubmitted"},
                        "action": {"type": "jumpTo", "target": "s1"},
                    }
                ],
            },
            {
                "id": "s1",
                "type": "scheduleMeeting",
                "headline": {"default": "Meet"},
                "config": {"calLink": "jane"},
            },
        ]
    )
    assert validate_survey(ok)["ok"] is True

    # scheduleMeeting only permits isSubmitted/isSkipped — `equals` is invalid.
    bad = _base(
        questions=[
            {
                "id": "s1",
                "type": "scheduleMeeting",
                "headline": {"default": "Meet"},
                "config": {"calLink": "jane"},
                "logic": [
                    {
                        "id": "r1",
                        "condition": {"leftQuestionId": "s1", "operator": "equals", "value": "x"},
                        "action": {"type": "jumpTo", "target": "q2"},
                    }
                ],
            },
            {"id": "q2", "type": "openText", "headline": {"default": "B"}, "config": {}},
        ]
    )
    result = validate_survey(bad)
    assert result["ok"] is False
    assert any(e["code"] == "invalid_operator" for e in result["errors"])


# --------------------------------------------------------------------------- #
#  hidden fields (survey-level) + recall tokens                               #
#  (a) a survey with hiddenFields + recall tokens in headlines passes BOTH     #
#      layers; (b) a malformed hiddenFields entry is REJECTED — proving the    #
#      hidden_fields -> hiddenFields translation makes the subschema fire      #
#      (additionalProperties:false), not pass vacuously through the open root. #
# --------------------------------------------------------------------------- #
def test_hidden_fields_and_recall_tokens_accepted():
    good = _base(
        hidden_fields=[
            {"id": "userId", "name": "User ID"},
            {"id": "plan"},  # name is optional
        ],
        questions=[
            {
                "id": "q1",
                "type": "openText",
                # Recall tokens embedded in the headline + subheader are plain text to
                # jsonschema and must validate as ordinary i18n strings.
                "headline": {"default": "Hi #recall:userId/fallback:there#, on #recall:plan/fallback:free# plan"},
                "subheader": {"default": "Tell us more, #recall:userId/fallback:friend#"},
                "config": {},
            }
        ],
    )
    assert validate_against_schema(good) == []
    validate_survey_definition(good)  # both layers; does not raise


def test_malformed_hidden_field_rejected():
    """A hiddenFields entry with an unknown property must be rejected — only caught
    because hidden_fields -> hiddenFields is translated before jsonschema (the item
    subschema is additionalProperties:false). Under the raw snake_case key the open
    root would pass it vacuously."""
    # 'key' is not a valid hiddenField property (the field is 'id').
    bad_prop = _base(hidden_fields=[{"id": "userId", "key": "userId"}])
    assert validate_against_schema(bad_prop), "hidden field with an unknown property must be rejected"

    # Missing the required 'id'.
    bad_missing = _base(hidden_fields=[{"name": "User ID"}])
    assert validate_against_schema(bad_missing), "hidden field without an id must be rejected"

    # An id that violates the questionId charset (starts with a digit) must fail.
    bad_id = _base(hidden_fields=[{"id": "1bad"}])
    assert validate_against_schema(bad_id), "hidden field id violating the id charset must be rejected"


def test_hidden_field_id_collision_rejected_by_logic():
    """A hidden field id that collides with a question id is a duplicate_id (in-code
    check — the recall/Response.data namespace is shared)."""
    payload = _base(
        hidden_fields=[{"id": "q1"}],  # collides with the question id below
        questions=[
            {"id": "q1", "type": "openText", "headline": {"default": "A"}, "config": {}},
        ],
    )
    # Schema is structurally fine; the logic layer flags the collision.
    assert validate_against_schema(payload) == []
    result = validate_survey(payload)
    assert result["ok"] is False
    assert any(e["code"] == "duplicate_id" for e in result["errors"])


# --------------------------------------------------------------------------- #
#  in-code validate_survey layer (the checks jsonschema cannot do)            #
# --------------------------------------------------------------------------- #
def test_duplicate_question_ids_rejected():
    payload = _base(
        questions=[
            {"id": "dup", "type": "openText", "headline": {"default": "A"}, "config": {}},
            {"id": "dup", "type": "openText", "headline": {"default": "B"}, "config": {}},
        ]
    )
    result = validate_survey(payload)
    assert result["ok"] is False
    assert any(e["code"] == "duplicate_id" for e in result["errors"])


def test_backward_jump_rejected():
    payload = _base(
        questions=[
            {"id": "q1", "type": "openText", "headline": {"default": "A"}, "config": {}},
            {
                "id": "q2",
                "type": "openText",
                "headline": {"default": "B"},
                "config": {},
                "logic": [
                    {
                        "id": "r1",
                        "condition": {"leftQuestionId": "q2", "operator": "isSubmitted"},
                        "action": {"type": "jumpTo", "target": "q1"},  # backward!
                    }
                ],
            },
        ]
    )
    result = validate_survey(payload)
    assert result["ok"] is False
    assert any(e["code"] == "backward_jump" for e in result["errors"])


def test_unknown_jump_target_rejected():
    payload = _base(
        questions=[
            {
                "id": "q1",
                "type": "openText",
                "headline": {"default": "A"},
                "config": {},
                "logic": [
                    {
                        "id": "r1",
                        "condition": {"leftQuestionId": "q1", "operator": "isSubmitted"},
                        "action": {"type": "jumpTo", "target": "nope"},
                    }
                ],
            }
        ]
    )
    result = validate_survey(payload)
    assert result["ok"] is False
    assert any(e["code"] == "unknown_target" for e in result["errors"])


def test_invalid_operator_for_type_rejected():
    """`greaterThan` is a numeric operator, invalid on an openText question."""
    payload = _base(
        questions=[
            {
                "id": "q1",
                "type": "openText",
                "headline": {"default": "A"},
                "config": {},
                "logic": [
                    {
                        "id": "r1",
                        "condition": {"leftQuestionId": "q1", "operator": "greaterThan", "value": 3},
                        "action": {"type": "jumpTo", "target": "q2"},
                    }
                ],
            },
            {"id": "q2", "type": "openText", "headline": {"default": "B"}, "config": {}},
        ]
    )
    result = validate_survey(payload)
    assert result["ok"] is False
    assert any(e["code"] == "invalid_operator" for e in result["errors"])


def test_forward_condition_ref_rejected():
    """A condition may not reference a LATER question than the rule's host."""
    payload = _base(
        questions=[
            {
                "id": "q1",
                "type": "openText",
                "headline": {"default": "A"},
                "config": {},
                "logic": [
                    {
                        "id": "r1",
                        "condition": {"leftQuestionId": "q2", "operator": "isSubmitted"},  # q2 is later
                        "action": {"type": "jumpTo", "target": "q2"},
                    }
                ],
            },
            {"id": "q2", "type": "openText", "headline": {"default": "B"}, "config": {}},
        ]
    )
    result = validate_survey(payload)
    assert result["ok"] is False
    assert any(e["code"] == "forward_condition_ref" for e in result["errors"])


# --------------------------------------------------------------------------- #
#  validate_survey_definition combines both layers and raises                 #
# --------------------------------------------------------------------------- #
def test_definition_raises_on_schema_error():
    payload = _base()
    del payload["languages"]
    with pytest.raises(SurveyValidationError) as exc:
        validate_survey_definition(payload)
    assert exc.value.schema_errors  # schema errors surfaced
    assert not exc.value.logic_errors  # logic not run when schema fails


def test_definition_raises_on_logic_error():
    payload = _base(
        questions=[
            {"id": "dup", "type": "openText", "headline": {"default": "A"}, "config": {}},
            {"id": "dup", "type": "openText", "headline": {"default": "B"}, "config": {}},
        ]
    )
    with pytest.raises(SurveyValidationError) as exc:
        validate_survey_definition(payload)
    # Schema passes (structurally fine), so logic_errors carry the duplicate.
    assert exc.value.logic_errors
    assert any(e["code"] == "duplicate_id" for e in exc.value.logic_errors)
