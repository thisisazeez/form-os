"""
In-code validation for the website/app-survey fields (FEATURE_PARITY WS-11): ``survey_type``,
``triggers``, ``display_options``.

These three fields are NOT in survey-schema.json (the top-level schema is
``additionalProperties: true``, so they pass through vacuously — see validators.py). We
therefore validate their STRUCTURE here, mirroring how ``blocks_validation`` /
``quotas_validation`` add the in-code checks the schema cannot express.

SCOPE (load-bearing): this validator is PURE (no DB, no model imports) so ``validators.py``
can call it on the write path. It checks the SHAPE only:
  - ``survey_type`` ∈ {link, app}.
  - ``triggers`` is a list of UUID-shaped strings (the ActionClass ids that fire the survey).
  - ``display_options`` is an object with optional ``displayLimit`` (int >= 1) and
    ``displayPercentage`` (number 0..100); no other keys.

The DB-backed check — "every trigger id resolves to an ActionClass in THIS survey's
environment" — needs the environment + a query, so it lives in the SERVICE layer
(apps.surveys.services._validate_app_survey_refs), not here. Documented so reviewers see the
split: shape here, cross-reference there.

ADDITIVE + backward-compatible: a survey with ``survey_type`` absent/``"link"`` and empty
``triggers``/``display_options`` produces NO errors and behaves byte-identically to today.
Error dicts use the same ``{code, message, ...}`` shape as the other validators; codes are
namespaced ``app_survey_*``.
"""
from __future__ import annotations

import uuid
from typing import Any

_VALID_SURVEY_TYPES = {"link", "app"}
_VALID_DISPLAY_OPTION_KEYS = {"displayLimit", "displayPercentage"}


def _is_uuid_str(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def validate_app_survey(survey: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the app-survey fields' SHAPE. Returns a list of error dicts (empty == valid).

    Reads ``survey_type`` / ``triggers`` / ``display_options`` (the SAME keys in the snake_case
    model payload and the camelCase schema view — no rename, so one lookup serves both).

    Checks (frozen error codes):
      - ``app_survey_bad_type``           — ``survey_type`` not in {link, app}.
      - ``app_survey_bad_triggers``       — ``triggers`` not a list, or an entry not a UUID string.
      - ``app_survey_bad_display_options``— ``display_options`` not an object / unknown key /
        out-of-range or wrong-typed ``displayLimit`` / ``displayPercentage``.
    """
    errors: list[dict[str, Any]] = []

    survey_type = survey.get("survey_type", "link")
    if survey_type not in _VALID_SURVEY_TYPES:
        errors.append({
            "code": "app_survey_bad_type",
            "message": f"survey_type must be one of {sorted(_VALID_SURVEY_TYPES)} (got {survey_type!r})",
        })

    triggers = survey.get("triggers", [])
    if triggers is not None and not isinstance(triggers, list):
        errors.append({
            "code": "app_survey_bad_triggers",
            "message": "triggers must be a list of ActionClass id strings",
        })
    elif isinstance(triggers, list):
        for t in triggers:
            if not _is_uuid_str(t):
                errors.append({
                    "code": "app_survey_bad_triggers",
                    "message": f"trigger {t!r} is not a valid ActionClass id (uuid)",
                })

    options = survey.get("display_options", {})
    if options is not None and not isinstance(options, dict):
        errors.append({
            "code": "app_survey_bad_display_options",
            "message": "display_options must be an object",
        })
    elif isinstance(options, dict):
        for key in options:
            if key not in _VALID_DISPLAY_OPTION_KEYS:
                errors.append({
                    "code": "app_survey_bad_display_options",
                    "message": (
                        f"unknown display_options key {key!r} "
                        f"(allowed: {sorted(_VALID_DISPLAY_OPTION_KEYS)})"
                    ),
                })
        limit = options.get("displayLimit")
        if limit is not None:
            # bool is an int subclass; reject it explicitly (a boolean limit is a bug).
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                errors.append({
                    "code": "app_survey_bad_display_options",
                    "message": "displayLimit must be an integer >= 1",
                })
        pct = options.get("displayPercentage")
        if pct is not None:
            if isinstance(pct, bool) or not isinstance(pct, int | float) or not (0 <= pct <= 100):
                errors.append({
                    "code": "app_survey_bad_display_options",
                    "message": "displayPercentage must be a number between 0 and 100",
                })

    return errors
