"""
Survey definition validation on the write path (Wave 2c).

contracts/models.md §2.6 + the Survey model docstring: the JSON shape is NOT
DB-enforced, so EVERY write path (create/update) MUST validate the survey's
``questions`` / ``logic`` / ``languages`` / ``styling`` / ``welcome_card`` /
``endings`` / ``close_on`` against ``contracts/survey-schema.json`` (draft 2020-12,
the single source of truth — ADR-004) AND run the in-code ``validate_survey``
checks (unique ids, no backward/unknown jump, operator/type validity) that JSON
Schema cannot express. Invalid surveys are rejected with a clean 400.

KEY/SHAPE TRANSLATION (load-bearing — verified against the schema):
The Django model + openapi use snake_case keys ``welcome_card`` / ``close_on``,
but ``survey-schema.json`` keys them camelCase as ``welcomeCard`` /
``closeConditions``. The top-level schema object is ``additionalProperties: true``,
so a snake_case payload validates VACUOUSLY — jsonschema treats ``welcome_card`` as
an unknown passthrough key and never checks its CONTENTS (a malformed welcome card
would slip through). We therefore assemble a camelCase "schema view" of the payload
before calling jsonschema, so the ``welcomeCard`` / ``closeConditions`` subschemas
(both ``additionalProperties: false``) actually run.

This module exposes one entry point used by the serializer/service:

    validate_survey_definition(payload) -> None  (raises SurveyValidationError)

``payload`` is the snake_case dict (model field names). On any schema or in-code
violation it raises ``SurveyValidationError`` carrying the jsonschema messages
(``schema_errors``) and/or the in-code error dicts (``logic_errors``) — the view
maps these to the openapi ``ValidationErrorBody`` (``schema_errors`` + ``errors``).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .logic import validate_survey as _logic_validate_survey

# contracts/survey-schema.json lives at <repo_root>/contracts/. This file is at
# <repo_root>/apps/surveys/validators.py -> three parents up to the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "contracts" / "survey-schema.json"


class SurveyValidationError(Exception):
    """A survey definition failed schema and/or in-code (logic) validation.

    Attributes:
        schema_errors: list[str] of human-readable jsonschema messages.
        logic_errors:  list[dict] of validate_survey() error dicts ({code, message, ...}).
    """

    def __init__(
        self,
        message: str = "Invalid survey definition.",
        *,
        schema_errors: list[str] | None = None,
        logic_errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.schema_errors = schema_errors or []
        self.logic_errors = logic_errors or []


@lru_cache(maxsize=1)
def _load_validator() -> Draft202012Validator:
    """Load + compile the frozen survey schema once (cached for process lifetime)."""
    schema = json.loads(_SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# Mapping: model/openapi snake_case field name -> survey-schema.json camelCase key.
# Only the keys that differ are listed; everything else passes through unchanged.
_SNAKE_TO_SCHEMA_KEY = {
    "welcome_card": "welcomeCard",
    "close_on": "closeConditions",
    "hidden_fields": "hiddenFields",
    # Survey follow-ups (the editor's 4th tab). Without this mapping the snake_case
    # `follow_ups` would validate VACUOUSLY (top-level additionalProperties:true), so a
    # malformed follow-up would slip past jsonschema — mirror welcome_card -> welcomeCard.
    "follow_ups": "followUps",
}


def _to_schema_view(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a camelCase "schema view" of the snake_case ``payload`` so the
    ``welcomeCard`` / ``closeConditions`` subschemas (additionalProperties:false)
    actually validate their contents. ``None`` values for optional nullable keys
    are dropped (the schema's nullability lives on the model column, not here —
    e.g. ``closeConditions`` permits ``null`` but ``welcomeCard`` does not, and a
    null welcome_card simply means "no welcome card" and should not be validated)."""
    schema_view: dict[str, Any] = {}
    for key, value in payload.items():
        schema_key = _SNAKE_TO_SCHEMA_KEY.get(key, key)
        if value is None:
            # A null optional key carries no shape to validate; omit it. (closeConditions
            # accepts null in-schema, but omitting is equally valid and avoids surprising
            # the welcomeCard branch, which is not nullable.)
            continue
        schema_view[schema_key] = value
    return schema_view


def validate_against_schema(payload: dict[str, Any]) -> list[str]:
    """Validate the snake_case ``payload`` against survey-schema.json. Returns a
    list of human-readable error messages (empty == valid)."""
    validator = _load_validator()
    schema_view = _to_schema_view(payload)
    errors = sorted(validator.iter_errors(schema_view), key=lambda e: list(e.path))
    messages: list[str] = []
    for err in errors:
        location = "/".join(str(p) for p in err.path) or "(root)"
        messages.append(f"{location}: {err.message}")
    return messages


def validate_logic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the in-code ``validate_survey`` checks (unique ids, no backward/unknown
    jump, operator/type validity) against the snake_case ``payload``, plus the
    follow-up cross-reference checks (condition/recipient refs resolve; unique ids),
    the block cross-reference checks (questionIds resolve; no question in two
    blocks; unique block ids), and the quota cross-reference checks (unique quota ids;
    endingId resolves; condition refs resolve with a type-valid operator).

    The logic evaluator reads ``questions`` and ``endings`` (both snake==camel here,
    so no translation needed). The follow-up validator reads ``follow_ups`` /
    ``followUps`` against the question + hidden-field id namespace. The block validator
    reads ``blocks`` (same key in both snake/camel — no rename) against the question-id
    namespace. The quota validator reads ``quotas`` (same key in both snake/camel — no
    rename) against the ending + question/hidden-field id namespaces. Returns the
    combined list of error dicts (empty == valid)."""
    from apps.followups.validation import validate_follow_ups

    from .app_survey_validation import validate_app_survey
    from .blocks_validation import validate_blocks
    from .quotas_validation import validate_quotas

    errors: list[dict[str, Any]] = []
    result = _logic_validate_survey(payload)
    if not result.get("ok"):
        errors.extend(result.get("errors", []))
    errors.extend(validate_follow_ups(payload))
    errors.extend(validate_blocks(payload))
    errors.extend(validate_quotas(payload))
    # App-survey field SHAPE checks (survey_type / triggers / display_options). The DB-backed
    # trigger-resolution check (each id resolves to an ActionClass in the env) is in the service
    # layer, which has the environment (apps.surveys.services._validate_app_survey_refs).
    errors.extend(validate_app_survey(payload))
    return errors


def validate_survey_definition(payload: dict[str, Any]) -> None:
    """Full write-path validation: jsonschema FIRST, then the in-code logic checks.

    Raises :class:`SurveyValidationError` if either fails. We collect BOTH layers'
    errors when the schema passes but the logic fails; if the schema itself fails we
    surface only the schema errors (the survey is structurally malformed, so running
    the logic checks on it would be noise).

    ``payload`` MUST contain at least ``questions`` + ``languages`` (the schema's
    top-level required keys); the serializer guarantees that before calling here.
    """
    schema_errors = validate_against_schema(payload)
    if schema_errors:
        raise SurveyValidationError(
            "Survey definition does not match survey-schema.json.",
            schema_errors=schema_errors,
        )

    logic_errors = validate_logic(payload)
    if logic_errors:
        raise SurveyValidationError(
            "Survey logic is invalid.",
            logic_errors=logic_errors,
        )
