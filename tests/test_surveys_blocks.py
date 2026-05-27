"""
Unit tests for the survey question-BLOCKS overlay (feat/parity-blocks).

Covers the additive `blocks` array end-to-end on the validator layers (pure functions,
no DB):
  1. jsonschema against contracts/survey-schema.json #/$defs/block — accept well-formed
     blocks; reject malformed ones (bad randomize enum, unknown property, wrong types).
     `blocks` is the SAME key in snake/camel (no translation), so it validates directly.
  2. the in-code cross-reference checks (apps/surveys/blocks_validation.validate_blocks)
     JSON Schema can't express: questionIds resolve to real questions; a question is in at
     most one block; unique block ids.

The HEADLINE invariant: a survey with NO blocks (the default) is byte-identical to today's
flat behavior — adding the optional `blocks` key with `[]` changes nothing.
"""
from __future__ import annotations

import pytest

from apps.surveys.blocks_validation import validate_blocks
from apps.surveys.validators import (
    SurveyValidationError,
    validate_against_schema,
    validate_survey_definition,
)


def _base(**overrides):
    """A minimal VALID survey definition (snake_case, model/openapi field names) with two
    questions so blocks have something to group."""
    payload = {
        "name": "S",
        "questions": [
            {"id": "q1", "type": "openText", "headline": {"default": "Hi"}, "config": {}},
            {"id": "q2", "type": "openText", "headline": {"default": "Bye"}, "config": {}},
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
#  no-blocks default == flat behavior unchanged                               #
# --------------------------------------------------------------------------- #
def test_no_blocks_key_is_valid():
    """Omitting `blocks` entirely (the default for every pre-existing survey) is valid."""
    assert validate_against_schema(_base()) == []
    validate_survey_definition(_base())  # does not raise


def test_empty_blocks_array_is_valid():
    """An explicit empty `blocks: []` is valid and changes nothing (the default value)."""
    assert validate_against_schema(_base(blocks=[])) == []
    assert validate_blocks(_base(blocks=[])) == []
    validate_survey_definition(_base(blocks=[]))  # does not raise


# --------------------------------------------------------------------------- #
#  jsonschema layer (#/$defs/block)                                           #
# --------------------------------------------------------------------------- #
def test_wellformed_block_passes_schema():
    blocks = [
        {"id": "b1", "title": "Demographics", "questionIds": ["q1", "q2"], "randomize": "all"}
    ]
    assert validate_against_schema(_base(blocks=blocks)) == []
    validate_survey_definition(_base(blocks=blocks))  # cross-refs resolve too


def test_minimal_block_passes_schema():
    """A block needs only id + questionIds (title/randomize optional); empty questionIds ok."""
    blocks = [{"id": "b1", "questionIds": []}]
    assert validate_against_schema(_base(blocks=blocks)) == []


def test_block_missing_id_fails_schema():
    blocks = [{"questionIds": ["q1"]}]
    assert validate_against_schema(_base(blocks=blocks)), "block must require 'id'"


def test_block_missing_question_ids_fails_schema():
    blocks = [{"id": "b1"}]
    assert validate_against_schema(_base(blocks=blocks)), "block must require 'questionIds'"


def test_block_bad_randomize_enum_fails_schema():
    blocks = [{"id": "b1", "questionIds": ["q1"], "randomize": "sometimes"}]
    assert validate_against_schema(_base(blocks=blocks)), "randomize must be in the enum"


def test_block_unknown_property_fails_schema():
    """#/$defs/block is additionalProperties:false — a stray key is rejected."""
    blocks = [{"id": "b1", "questionIds": ["q1"], "bogus": 1}]
    assert validate_against_schema(_base(blocks=blocks)), "unknown block property must fail"


def test_block_questionids_not_array_fails_schema():
    blocks = [{"id": "b1", "questionIds": "q1"}]
    assert validate_against_schema(_base(blocks=blocks)), "questionIds must be an array"


def test_each_randomize_enum_value_accepted():
    for mode in ("none", "all", "exceptLast"):
        blocks = [{"id": "b1", "questionIds": ["q1"], "randomize": mode}]
        assert validate_against_schema(_base(blocks=blocks)) == [], f"{mode} should be valid"


# --------------------------------------------------------------------------- #
#  in-code cross-reference checks (validate_blocks)                           #
# --------------------------------------------------------------------------- #
def test_unknown_question_ref_rejected():
    blocks = [{"id": "b1", "questionIds": ["q1", "qX"]}]
    errors = validate_blocks(_base(blocks=blocks))
    codes = {e["code"] for e in errors}
    assert "block_unknown_question_ref" in codes
    # And the full definition validator surfaces it.
    with pytest.raises(SurveyValidationError):
        validate_survey_definition(_base(blocks=blocks))


def test_question_in_two_blocks_rejected():
    blocks = [
        {"id": "b1", "questionIds": ["q1"]},
        {"id": "b2", "questionIds": ["q1", "q2"]},
    ]
    errors = validate_blocks(_base(blocks=blocks))
    codes = {e["code"] for e in errors}
    assert "block_question_in_multiple_blocks" in codes
    with pytest.raises(SurveyValidationError):
        validate_survey_definition(_base(blocks=blocks))


def test_same_question_twice_in_one_block_rejected():
    blocks = [{"id": "b1", "questionIds": ["q1", "q1"]}]
    errors = validate_blocks(_base(blocks=blocks))
    codes = {e["code"] for e in errors}
    assert "block_question_in_multiple_blocks" in codes


def test_duplicate_block_id_rejected():
    blocks = [
        {"id": "b1", "questionIds": ["q1"]},
        {"id": "b1", "questionIds": ["q2"]},
    ]
    errors = validate_blocks(_base(blocks=blocks))
    codes = {e["code"] for e in errors}
    assert "block_duplicate_id" in codes


def test_disjoint_blocks_over_real_questions_pass():
    """The happy path: two blocks, each over distinct real questions, unique ids."""
    blocks = [
        {"id": "b1", "title": "First", "questionIds": ["q1"], "randomize": "none"},
        {"id": "b2", "title": "Second", "questionIds": ["q2"], "randomize": "all"},
    ]
    assert validate_blocks(_base(blocks=blocks)) == []
    validate_survey_definition(_base(blocks=blocks))  # does not raise


def test_block_id_may_match_unused_namespace():
    """A block id is NOT a Response.data key, so it is NOT cross-checked against the
    question/ending/hidden-field id namespace — only uniqueness AMONG blocks matters.
    A block whose id equals a question id is therefore allowed (distinct namespace)."""
    blocks = [{"id": "q1", "questionIds": ["q2"]}]
    assert validate_blocks(_base(blocks=blocks)) == []
    validate_survey_definition(_base(blocks=blocks))  # does not raise


def test_validate_blocks_accepts_camel_or_absent():
    """validate_blocks reads the single `blocks` key; absent/non-list -> no cross-checks."""
    assert validate_blocks(_base()) == []  # no blocks key
    assert validate_blocks({"questions": [], "blocks": None}) == []
    assert validate_blocks({"questions": [], "blocks": "nope"}) == []
