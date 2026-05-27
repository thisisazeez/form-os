"""Python runner for the VARIABLES TS<->PY parity suite (feat/parity-variables).

This is a SEPARATE corpus from tests/logic_fixtures/ (the frozen 64-case logic corpus, which
stays untouched). It exercises the NEW survey-variables + `calculate`-action behavior against
the SAME production evaluators the TS runner uses, proving byte-identical TS<->PY results:

  * compute_variables(survey, answers)               -> the live {variableId: value} map
  * next_step / required_question_ids / validate_survey  (the frozen contract surface)
  * resolve_recall(text, answers + variables)         -> recall-of-variable resolution

The evaluator is INJECTABLE (mirrors tests/logic_fixtures/run_py.py):
  * default  -> apps.surveys.logic (the production Python evaluator).
  * override -> set OPENSURVEY_LOGIC_EVALUATOR.
The recall resolver is apps.followups.recall.resolve_recall (the Python recall twin).

Run standalone:  python tests/logic_fixtures_variables/run_py.py
Or via pytest:    pytest tests/logic_fixtures_variables/run_py.py -p no:django
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
FIXTURES_DIR = HERE / "fixtures"
REPO_ROOT = HERE.parent.parent
SCHEMA_PATH = REPO_ROOT / "contracts" / "survey-schema.json"

# Make the repo root importable so `apps.*` resolves when run standalone.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_evaluator():
    module_path = os.environ.get("OPENSURVEY_LOGIC_EVALUATOR", "apps.surveys.logic")
    module = importlib.import_module(module_path)
    required = ("next_step", "required_question_ids", "validate_survey", "compute_variables")
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise AttributeError(
            f"Evaluator module {module_path!r} is missing required functions: {missing}."
        )
    return module


EVALUATOR = _load_evaluator()

# The Python recall twin (apps/followups/recall.py mirrors frontend/src/lib/recall.ts).
from apps.followups.recall import resolve_recall  # noqa: E402


def _load_schema_validator():
    try:
        import jsonschema  # noqa: WPS433
    except ImportError:
        return None
    if not SCHEMA_PATH.exists():
        return None
    schema = json.loads(SCHEMA_PATH.read_text())
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)

    def _validate(survey: dict[str, Any]) -> None:
        errors = sorted(validator.iter_errors(survey), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            raise AssertionError(
                f"survey does not validate against survey-schema.json: "
                f"{first.message} (at {list(first.path)})"
            )

    return _validate


SCHEMA_VALIDATE = _load_schema_validator()


def _load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    files = sorted(FIXTURES_DIR.glob("*.json"))
    assert files, f"no fixture files found in {FIXTURES_DIR}"
    seen: set[str] = set()
    for path in files:
        payload = json.loads(path.read_text())
        items = payload if isinstance(payload, list) else [payload]
        for case in items:
            name = case["name"]
            assert name not in seen, f"duplicate fixture name across corpus: {name!r}"
            seen.add(name)
            case["_file"] = path.name
            cases.append(case)
    return cases


CASES = _load_cases()


def _normalize_next(step: Any) -> dict[str, Any]:
    assert isinstance(step, dict), f"NextStep must be a dict, got {type(step)}: {step!r}"
    out = {"type": step["type"]}
    sid = step.get("id")
    if sid is not None:
        out["id"] = sid
    return out


def _normalize_vars(vars_map: dict[str, Any]) -> dict[str, Any]:
    """Numbers may come back as int or float; normalize to float for JSON-neutral compare
    so 15 == 15.0 across the JSON literal (int) and the float-arithmetic result."""
    out: dict[str, Any] = {}
    for k, v in vars_map.items():
        out[k] = float(v) if isinstance(v, int | float) and not isinstance(v, bool) else v
    return out


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_variables_fixture(case: dict[str, Any]) -> None:
    survey = case["survey"]

    # Validation-only case ------------------------------------------------- #
    if case.get("expectValid") is False:
        result = EVALUATOR.validate_survey(survey)
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

    # Positive case: the survey must be schema-valid (guard). -------------- #
    if SCHEMA_VALIDATE is not None:
        SCHEMA_VALIDATE(survey)
    vresult = EVALUATOR.validate_survey(survey)
    assert vresult.get("ok") is True, (
        f"validate_survey unexpectedly failed for {case['name']!r}: {vresult.get('errors')}"
    )

    answers = case.get("answers", {})

    # compute_variables assertion ----------------------------------------- #
    variables = EVALUATOR.compute_variables(survey, answers)
    if "expected_variables" in case:
        assert _normalize_vars(variables) == _normalize_vars(case["expected_variables"]), (
            f"compute_variables mismatch for {case['name']!r}: "
            f"expected {case['expected_variables']!r}, got {variables!r}"
        )

    # next_step assertion (optional) -------------------------------------- #
    if "expected_next" in case:
        actual = EVALUATOR.next_step(survey, answers, case.get("currentQuestionId"))
        assert _normalize_next(actual) == _normalize_next(case["expected_next"]), (
            f"next_step mismatch for {case['name']!r}: "
            f"expected {case['expected_next']!r}, got {actual!r}"
        )

    # required-set assertion (optional) ----------------------------------- #
    if "expected_required" in case:
        actual_required = EVALUATOR.required_question_ids(survey, answers)
        assert set(actual_required) == set(case["expected_required"]), (
            f"required_question_ids mismatch for {case['name']!r}: "
            f"expected {sorted(case['expected_required'])}, got {sorted(actual_required)}"
        )

    # recall-of-variable assertion (optional) ----------------------------- #
    if "expected_recall" in case:
        # The recall lookup map enriches the answers with the live variable values (exactly
        # what the runtime + follow-up send path do). resolve_recall reads by id.
        recall_answers = {**answers, **variables}
        rendered = resolve_recall(case["recall_text"], recall_answers)
        assert rendered == case["expected_recall"], (
            f"recall mismatch for {case['name']!r}: "
            f"expected {case['expected_recall']!r}, got {rendered!r}"
        )


def test_corpus_is_non_trivial() -> None:
    assert len(CASES) >= 15, f"expected a substantial variables corpus, found {len(CASES)}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-p", "no:django"]))
