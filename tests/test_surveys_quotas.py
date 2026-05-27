"""
Unit tests for survey QUOTAS — the schema layer + in-code cross-reference validation.

Covers (pure functions — no DB):
  1. jsonschema against contracts/survey-schema.json #/$defs/quota: a valid quota passes; a
     structurally-malformed quota (missing required key, limit < 1, bad condition shape) fails.
     `quotas` is the SAME key in snake/camel, so it is listed in the schema's top-level
     `properties` (NOT a vacuous additionalProperties:true passthrough) — these assert it runs.
  2. the in-code validate_quotas checks JSON Schema can't express: unique quota ids; endingId
     resolves to a declared ending; condition refs resolve to a real question/hidden-field id
     with a type-valid operator (reusing the SAME _OPS_BY_TYPE table the logic validator uses).
  3. quotas with no quotas array are byte-identical to today (a survey with no `quotas` validates
     exactly as before).
"""
from __future__ import annotations

import pytest

from apps.surveys.quotas_validation import validate_quotas
from apps.surveys.validators import (
    SurveyValidationError,
    validate_against_schema,
    validate_survey_definition,
)


def _base(**overrides):
    """A minimal VALID survey definition (snake_case, model/openapi field names) with an
    ending + a singleSelect question so quota conditions + endingId refs have real targets."""
    payload = {
        "name": "S",
        "questions": [
            {
                "id": "country",
                "type": "singleSelect",
                "headline": {"default": "Country?"},
                "config": {
                    "choices": [
                        {"id": "us", "label": {"default": "US"}},
                        {"id": "uk", "label": {"default": "UK"}},
                    ]
                },
            },
            {
                "id": "age",
                "type": "number",
                "headline": {"default": "Age?"},
                "config": {},
            },
        ],
        "logic": [],
        "languages": [{"code": "en", "label": "English", "default": True}],
        "styling": {},
        "welcome_card": None,
        "endings": [
            {"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}},
            {"id": "quota_full", "type": "endScreen", "headline": {"default": "Full"}},
        ],
        "close_on": None,
        "hidden_fields": [{"id": "plan", "name": "Plan"}],
    }
    payload.update(overrides)
    return payload


def _quota(**overrides):
    q = {
        "id": "q_us",
        "name": "US respondents",
        "limit": 2,
        "condition": {"leftQuestionId": "country", "operator": "equals", "value": "us"},
        "endingId": "quota_full",
    }
    q.update(overrides)
    return q


# --------------------------------------------------------------------------- #
#  jsonschema layer                                                           #
# --------------------------------------------------------------------------- #
def test_valid_quota_passes_schema_and_full_validation():
    payload = _base(quotas=[_quota()])
    assert validate_against_schema(payload) == []
    validate_survey_definition(payload)  # does not raise


def test_no_quotas_is_unchanged():
    """A survey with no `quotas` key validates exactly as before (byte-identical guarantee)."""
    payload = _base()
    assert "quotas" not in payload
    assert validate_against_schema(payload) == []
    validate_survey_definition(payload)


def test_empty_quotas_array_passes():
    payload = _base(quotas=[])
    assert validate_against_schema(payload) == []
    validate_survey_definition(payload)


def test_quota_missing_required_key_fails_schema():
    # Drop `condition` — a schema-required key on #/$defs/quota.
    bad = _quota()
    del bad["condition"]
    errors = validate_against_schema(_base(quotas=[bad]))
    assert errors, "schema must require quota.condition"


def test_quota_limit_below_one_fails_schema():
    errors = validate_against_schema(_base(quotas=[_quota(limit=0)]))
    assert errors, "schema must reject limit < 1"


def test_quota_limit_must_be_integer_fails_schema():
    errors = validate_against_schema(_base(quotas=[_quota(limit=2.5)]))
    assert errors, "schema must reject a non-integer limit"


def test_quota_unknown_extra_key_fails_schema():
    # #/$defs/quota is additionalProperties:false.
    errors = validate_against_schema(_base(quotas=[_quota(bogus="x")]))
    assert errors, "schema must reject unknown quota keys (additionalProperties:false)"


def test_quota_malformed_condition_fails_schema():
    # A condition that is neither a leaf nor a connector.
    errors = validate_against_schema(_base(quotas=[_quota(condition={"foo": "bar"})]))
    assert errors, "schema must reject a malformed conditionGroup"


# --------------------------------------------------------------------------- #
#  in-code cross-reference validation                                         #
# --------------------------------------------------------------------------- #
def test_validate_quotas_accepts_valid():
    assert validate_quotas(_base(quotas=[_quota()])) == []


def test_validate_quotas_no_endingid_is_valid():
    """endingId is optional — its absence means 'built-in quota full screen'."""
    q = _quota()
    del q["endingId"]
    assert validate_quotas(_base(quotas=[q])) == []


def test_validate_quotas_duplicate_id():
    errors = validate_quotas(_base(quotas=[_quota(), _quota(name="dup")]))
    codes = {e["code"] for e in errors}
    assert "quota_duplicate_id" in codes


def test_validate_quotas_unknown_ending():
    errors = validate_quotas(_base(quotas=[_quota(endingId="nope")]))
    codes = {e["code"] for e in errors}
    assert "quota_unknown_ending" in codes


def test_validate_quotas_unknown_condition_ref():
    bad = _quota(
        condition={"leftQuestionId": "ghost", "operator": "equals", "value": "x"}
    )
    errors = validate_quotas(_base(quotas=[bad]))
    codes = {e["code"] for e in errors}
    assert "quota_unknown_condition_ref" in codes


def test_validate_quotas_invalid_operator_for_type():
    # `country` is singleSelect; `greaterThan` is not a singleSelect operator.
    bad = _quota(
        condition={"leftQuestionId": "country", "operator": "greaterThan", "value": 1}
    )
    errors = validate_quotas(_base(quotas=[bad]))
    codes = {e["code"] for e in errors}
    assert "quota_invalid_operator" in codes


def test_validate_quotas_numeric_operator_valid_for_number():
    # `age` is number; `greaterThan` IS valid for number.
    good = _quota(
        id="q_age",
        condition={"leftQuestionId": "age", "operator": "greaterThan", "value": 30},
    )
    assert validate_quotas(_base(quotas=[good])) == []


def test_validate_quotas_hidden_field_ref_text_operator_valid():
    # A hidden-field ref accepts the text-operator family (treated as a text operand).
    good = _quota(
        id="q_plan",
        condition={"leftQuestionId": "plan", "operator": "equals", "value": "pro"},
    )
    assert validate_quotas(_base(quotas=[good])) == []


def test_validate_quotas_connector_condition_refs_checked():
    """Cross-refs are checked inside an and/or connector tree, not just on leaves."""
    bad = _quota(
        condition={
            "connector": "and",
            "conditions": [
                {"leftQuestionId": "country", "operator": "equals", "value": "us"},
                {"leftQuestionId": "ghost", "operator": "equals", "value": "x"},
            ],
        }
    )
    errors = validate_quotas(_base(quotas=[bad]))
    codes = {e["code"] for e in errors}
    assert "quota_unknown_condition_ref" in codes


def test_full_validation_surfaces_quota_logic_errors():
    """validate_survey_definition collects quota cross-ref errors as logic_errors (400 body)."""
    bad = _quota(endingId="nope")
    with pytest.raises(SurveyValidationError) as exc_info:
        validate_survey_definition(_base(quotas=[bad]))
    err = exc_info.value
    codes = {e["code"] for e in err.logic_errors}
    assert "quota_unknown_ending" in codes
