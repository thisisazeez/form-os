"""
In-tree parity test for survey VARIABLES + the `calculate` action (feat/parity-variables).

Parallel to ``tests/test_surveys_logic_parity.py`` (which re-asserts the frozen 64-case logic
corpus against ``apps.surveys.logic``). This module re-asserts the SEPARATE variables corpus
(``tests/logic_fixtures_variables/fixtures/*.json``) against the PRODUCTION Python evaluator from
the normal Django test run, so CI catches any variable-evaluator / recall drift WITHOUT env-var
gymnastics. The TS twin (``tests/logic_fixtures_variables/run_ts.ts``) runs the SAME corpus, so a
divergence between TS and Python is caught.

It validates each positive-case survey against contracts/survey-schema.json too (the same guard the
standalone harness applies), so a fixture can't silently encode an illegal survey shape.

NOTE: this is intentionally NOT in tests/logic_fixtures/ — the frozen 64-case corpus + its
``== 64`` count assertion stay UNTOUCHED.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.followups.recall import resolve_recall
from apps.surveys.logic import (
    compute_variables,
    next_step,
    required_question_ids,
    validate_survey,
)
from apps.surveys.validators import validate_against_schema

_FIXTURES_DIR = Path(__file__).resolve().parent / "logic_fixtures_variables" / "fixtures"


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
    assert isinstance(step, dict), f"NextStep must be a dict, got {type(step)}: {step!r}"
    out = {"type": step["type"]}
    sid = step.get("id")
    if sid is not None:
        out["id"] = sid
    return out


def _normalize_vars(vars_map: dict) -> dict:
    """Numbers may be int (JSON literal) or float (arithmetic); compare as float so 15 == 15.0."""
    out: dict = {}
    for k, v in vars_map.items():
        out[k] = float(v) if isinstance(v, int | float) and not isinstance(v, bool) else v
    return out


def test_variables_corpus_is_non_trivial():
    assert len(_CASES) >= 15, f"expected a substantial variables corpus, found {len(_CASES)}"


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_production_evaluator_matches_variable_fixture(case: dict):
    survey = case["survey"]

    # Validation-only case.
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

    # Positive case: schema-valid + validate_survey passes.
    schema_errors = validate_against_schema(survey)
    assert not schema_errors, (
        f"fixture survey {case['name']!r} does not validate against survey-schema.json: "
        f"{schema_errors}"
    )
    vresult = validate_survey(survey)
    assert vresult.get("ok") is True, (
        f"validate_survey unexpectedly failed for {case['name']!r}: {vresult.get('errors')}"
    )

    answers = case.get("answers", {})
    variables = compute_variables(survey, answers)

    if "expected_variables" in case:
        assert _normalize_vars(variables) == _normalize_vars(case["expected_variables"]), (
            f"compute_variables mismatch for {case['name']!r}: "
            f"expected {case['expected_variables']!r}, got {variables!r}"
        )

    if "expected_next" in case:
        actual = next_step(survey, answers, case.get("currentQuestionId"))
        assert _normalize_next(actual) == _normalize_next(case["expected_next"]), (
            f"next_step mismatch for {case['name']!r}: expected {case['expected_next']!r}, "
            f"got {actual!r}"
        )

    if "expected_required" in case:
        actual_required = required_question_ids(survey, answers)
        assert set(actual_required) == set(case["expected_required"]), (
            f"required_question_ids mismatch for {case['name']!r}: "
            f"expected {sorted(case['expected_required'])}, got {sorted(actual_required)}"
        )

    if "expected_recall" in case:
        recall_answers = {**answers, **variables}
        rendered = resolve_recall(case["recall_text"], recall_answers)
        assert rendered == case["expected_recall"], (
            f"recall mismatch for {case['name']!r}: expected {case['expected_recall']!r}, "
            f"got {rendered!r}"
        )
