"""
In-tree parity test: the PRODUCTION evaluator (``apps.surveys.logic``) against the SAME frozen
fixture corpus the shared TS<->PY harness uses (``tests/logic_fixtures/fixtures/*.json``).

WHY this exists in addition to the harness: ``tests/logic_fixtures/run_py.py`` proves parity
when invoked with ``OPENSURVEY_LOGIC_EVALUATOR=apps.surveys.logic`` (the canonical Wave-3
injection). This module re-asserts the SAME corpus against ``apps.surveys.logic`` directly from
the normal Django test run, so CI catches any evaluator drift WITHOUT env-var gymnastics — and
without editing the harness (we only READ its fixture JSON, per the brief's territory rules).

It validates positive-case surveys against contracts/survey-schema.json too (the same guard the
harness applies), so a fixture can't silently encode an illegal survey shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.surveys.logic import next_step, required_question_ids, validate_survey
from apps.surveys.validators import validate_against_schema

_FIXTURES_DIR = Path(__file__).resolve().parent / "logic_fixtures" / "fixtures"


def _load_cases() -> list[dict]:
    cases: list[dict] = []
    files = sorted(_FIXTURES_DIR.glob("*.json"))
    assert files, f"no fixture files found in {_FIXTURES_DIR}"
    for path in files:
        payload = json.loads(path.read_text())
        items = payload if isinstance(payload, list) else [payload]
        for case in items:
            case["_file"] = path.name
            cases.append(case)
    return cases


_CASES = _load_cases()


def _normalize_next(step) -> dict:
    """Normalize a NextStep so an absent 'id' and id=None compare equal (README §2)."""
    assert isinstance(step, dict), f"NextStep must be a dict, got {type(step)}: {step!r}"
    out = {"type": step["type"]}
    sid = step.get("id")
    if sid is not None:
        out["id"] = sid
    return out


def test_corpus_is_non_trivial():
    """Guard: the corpus must actually contain the full 64-case suite."""
    assert len(_CASES) == 64, f"expected the frozen 64-case corpus, found {len(_CASES)}"


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_production_evaluator_matches_fixture(case: dict):
    survey = case["survey"]

    # Validation-only case: validate_survey must FAIL with the expected code.
    if case.get("expectValid") is False:
        result = validate_survey(survey)
        assert result.get("ok") is False, (
            f"expected validate_survey to FAIL for {case['name']!r}, got ok=True"
        )
        expected_code = case.get("expectedErrorCode")
        if expected_code is not None:
            codes = [e.get("code") for e in result.get("errors", [])]
            assert expected_code in codes, (
                f"expected error code {expected_code!r} in {codes!r} for {case['name']!r}"
            )
        return

    # Positive case: the survey itself must be schema-valid (same guard as run_py.py).
    schema_errors = validate_against_schema(survey)
    assert not schema_errors, (
        f"fixture survey {case['name']!r} does not validate against survey-schema.json: "
        f"{schema_errors}"
    )

    # validate_survey should pass for positive cases.
    vresult = validate_survey(survey)
    assert vresult.get("ok") is True, (
        f"validate_survey unexpectedly failed for {case['name']!r}: {vresult.get('errors')}"
    )

    if case.get("expectValid") is True and "expected_next" not in case:
        return

    # next_step assertion.
    actual = next_step(survey, case["answers"], case["currentQuestionId"])
    assert _normalize_next(actual) == _normalize_next(case["expected_next"]), (
        f"next_step mismatch for {case['name']!r}: expected {case['expected_next']!r}, got {actual!r}"
    )

    # Optional required-set assertion.
    if "expected_required" in case:
        actual_required = required_question_ids(survey, case["answers"])
        assert set(actual_required) == set(case["expected_required"]), (
            f"required_question_ids mismatch for {case['name']!r}: "
            f"expected {sorted(case['expected_required'])}, got {sorted(actual_required)}"
        )
