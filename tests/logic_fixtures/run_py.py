"""Python runner for the shared TS<->PY logic-fixture suite.

Loads EVERY case in ``fixtures/*.json`` and asserts the (injectable) Python
evaluator's behavior matches the language-neutral ``expected_next`` /
``expected_required`` / validation expectations. The TS runner (``run_ts.ts``)
runs the SAME corpus; CI runs both so parity is enforced (REVIEW.md §6).

The evaluator is INJECTABLE (README §6):
  * default  -> ``reference.evaluator`` (this dir's reference implementation)
  * override -> set ``OPENSURVEY_LOGIC_EVALUATOR`` to the real module once Wave 3
                ships it (e.g. ``apps.surveys.logic.evaluator``). It MUST export
                ``next_step``, ``required_question_ids``, ``validate_survey``.

Discovered by pytest as ``tests/logic_fixtures/run_py.py`` (see the scaffold's
pytest config; this file matches the ``run_*`` test path the CI Python job runs).

Deps: pytest, jsonschema. (Listed for the scaffold to add — this agent does not
own dependency files.)
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# --------------------------------------------------------------------------- #
#  Paths                                                                       #
# --------------------------------------------------------------------------- #
HERE = Path(__file__).resolve().parent
FIXTURES_DIR = HERE / "fixtures"
# contracts/survey-schema.json lives at <repo_root>/contracts/. This file is at
# <repo_root>/tests/logic_fixtures/run_py.py -> two parents up to the repo root.
REPO_ROOT = HERE.parent.parent
SCHEMA_PATH = REPO_ROOT / "contracts" / "survey-schema.json"

# Make the reference package importable when pytest's rootdir differs.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


# --------------------------------------------------------------------------- #
#  Evaluator injection                                                         #
# --------------------------------------------------------------------------- #
def _load_evaluator():
    module_path = os.environ.get("OPENSURVEY_LOGIC_EVALUATOR", "reference.evaluator")
    module = importlib.import_module(module_path)
    missing = [
        name
        for name in ("next_step", "required_question_ids", "validate_survey")
        if not hasattr(module, name)
    ]
    if missing:
        raise AttributeError(
            f"Evaluator module {module_path!r} is missing required functions: {missing}. "
            "It must export next_step, required_question_ids, validate_survey (README §7)."
        )
    return module


EVALUATOR = _load_evaluator()


# --------------------------------------------------------------------------- #
#  JSON Schema validator (guards that positive-case surveys are well-formed)   #
# --------------------------------------------------------------------------- #
def _load_schema_validator():
    """Return a callable(survey)->None that raises on schema violation, or None
    if jsonschema/the schema file is unavailable (then we skip the guard)."""
    try:
        import jsonschema  # noqa: WPS433 (local import is intentional)
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
                f"survey does not validate against contracts/survey-schema.json: "
                f"{first.message} (at {list(first.path)})"
            )

    return _validate


SCHEMA_VALIDATE = _load_schema_validator()


# --------------------------------------------------------------------------- #
#  Fixture loading                                                             #
# --------------------------------------------------------------------------- #
def _load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    files = sorted(FIXTURES_DIR.glob("*.json"))
    assert files, f"no fixture files found in {FIXTURES_DIR}"
    seen_names: set[str] = set()
    for path in files:
        payload = json.loads(path.read_text())
        items = payload if isinstance(payload, list) else [payload]
        for case in items:
            name = case["name"]
            assert name not in seen_names, f"duplicate fixture name across corpus: {name!r}"
            seen_names.add(name)
            case["_file"] = path.name
            cases.append(case)
    return cases


CASES = _load_cases()


def _normalize_next(step: Any) -> dict[str, Any]:
    """Normalize a NextStep so an absent 'id' and id=None compare equal (README §2)."""
    assert isinstance(step, dict), f"NextStep must be a dict, got {type(step)}: {step!r}"
    out = {"type": step["type"]}
    sid = step.get("id")
    if sid is not None:
        out["id"] = sid
    return out


# --------------------------------------------------------------------------- #
#  Tests                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_logic_fixture(case: dict[str, Any]) -> None:
    survey = case["survey"]

    # Validation-only case -------------------------------------------------- #
    if case.get("expectValid") is False:
        result = EVALUATOR.validate_survey(survey)
        assert result.get("ok") is False, (
            f"expected validateSurvey to FAIL for {case['name']!r}, got ok=True"
        )
        expected_code = case.get("expectedErrorCode")
        if expected_code is not None:
            codes = [e.get("code") for e in result.get("errors", [])]
            assert expected_code in codes, (
                f"expected error code {expected_code!r} in {codes!r} for {case['name']!r}"
            )
        return

    # Positive case: the survey itself must be schema-valid (guard). -------- #
    if SCHEMA_VALIDATE is not None:
        SCHEMA_VALIDATE(survey)

    # validateSurvey should pass for positive cases (or explicitly expectValid:true).
    vresult = EVALUATOR.validate_survey(survey)
    assert vresult.get("ok") is True, (
        f"validateSurvey unexpectedly failed for {case['name']!r}: {vresult.get('errors')}"
    )

    if case.get("expectValid") is True and "expected_next" not in case:
        # Pure positive-validation case.
        return

    # next_step assertion --------------------------------------------------- #
    actual = EVALUATOR.next_step(survey, case["answers"], case["currentQuestionId"])
    assert _normalize_next(actual) == _normalize_next(case["expected_next"]), (
        f"next_step mismatch for {case['name']!r}: "
        f"expected {case['expected_next']!r}, got {actual!r}"
    )

    # optional required-set assertion --------------------------------------- #
    if "expected_required" in case:
        actual_required = EVALUATOR.required_question_ids(survey, case["answers"])
        assert set(actual_required) == set(case["expected_required"]), (
            f"required_question_ids mismatch for {case['name']!r}: "
            f"expected {sorted(case['expected_required'])}, got {sorted(actual_required)}"
        )


def test_corpus_is_non_trivial() -> None:
    """Guard: the corpus must actually contain cases (catches a glob/path regression)."""
    assert len(CASES) >= 20, f"expected a substantial fixture corpus, found {len(CASES)}"


if __name__ == "__main__":
    # `-p no:django` keeps this harness runnable standalone: the repo ships a
    # Django project (manage.py at root), so pytest-django would otherwise
    # auto-discover it and fail importing the scaffold's settings deps before any
    # fixture runs. This corpus has no Django dependency. CI invokes pytest
    # directly with its own config, so this only affects the direct-run path.
    sys.exit(pytest.main([__file__, "-v", "-p", "no:django"]))
