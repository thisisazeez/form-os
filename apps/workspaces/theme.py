"""
Project (workspace) "Look & Feel" theme — the SAME Styling shape a survey uses, plus the
inheritance merge that composes a project theme UNDER a survey's own styling.

WHY THIS LIVES HERE: the project theme is stored on ``Workspace.survey_theme`` (a JSONField),
and at serve time the runtime needs ``merge_styling(project_theme, survey_styling)``. Both the
storage shape and the merge are workspace concerns, so they share one module.

SHAPE (NOT a new invention): the theme dict is exactly ``contracts/survey-schema.json``
``#/$defs/styling`` — ``brandColor`` / ``questionColor`` / ``backgroundColor`` / ``buttonColor``
/ ``buttonTextColor`` / ``background`` ({type,value}) / ``logoUrl`` / ``roundness`` /
``hideProgressBar`` / ``cardArrangement``. That is why a project theme composes with a survey's
own styling: they are the same object.

PRECEDENCE (shallow, top-level): a survey's own styling key WINS whole. The merge is a shallow
``{**project, **survey}`` so that, e.g., a survey ``background`` (itself a nested {type,value})
shadows the project ``background`` ENTIRELY — we do NOT deep-merge inside ``background``. The
default (no project theme AND no survey styling) is ``{}`` — current behavior, unchanged.

VALIDATION: ``validate_theme`` runs the theme dict through the frozen survey schema's
``#/$defs/styling`` subschema (additionalProperties:false there — unknown keys / out-of-range
roundness / bad enum are rejected), so the project theme can never hold a shape the survey
runtime could not. We reuse the same compiled Draft 2020-12 validator infrastructure the survey
write path uses (one source of truth, ADR-004).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from jsonschema import Draft202012Validator

# contracts/survey-schema.json lives at <repo_root>/contracts/. This file is at
# <repo_root>/apps/workspaces/theme.py → parents[2] == <repo_root> (mirrors validators.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "contracts" / "survey-schema.json"


@lru_cache(maxsize=1)
def _styling_validator() -> Draft202012Validator:
    """Compile a validator for the ``#/$defs/styling`` subschema (cached for the process).

    We lift the ``styling`` subschema out and carry the full ``$defs`` alongside it so any
    internal ``$ref`` still resolves (the styling object happens to be self-contained today,
    but carrying ``$defs`` keeps this robust if it grows a ref later)."""
    schema = json.loads(_SCHEMA_PATH.read_text())
    styling = dict(schema["$defs"]["styling"])
    styling.setdefault("$defs", schema["$defs"])
    Draft202012Validator.check_schema(styling)
    return Draft202012Validator(styling)


def validate_theme(theme: Any) -> None:
    """Validate a project theme against ``#/$defs/styling``. Raises ``ValidationError`` on a bad
    shape (unknown key, out-of-range roundness, bad enum, wrong type). ``{}`` is valid (no theme).

    Raised as a Django ``ValidationError`` so the settings POST view + the DRF write path can map
    it to a 400 the same way they map other field errors."""
    if not isinstance(theme, dict):
        raise ValidationError({"survey_theme": "Theme must be an object."})
    errors = sorted(
        _styling_validator().iter_errors(theme), key=lambda e: list(e.path)
    )
    if errors:
        messages = []
        for err in errors:
            location = "/".join(str(p) for p in err.path) or "(root)"
            messages.append(f"{location}: {err.message}")
        raise ValidationError({"survey_theme": messages})


def merge_styling(
    project_theme: dict | None, survey_styling: dict | None
) -> dict:
    """Compose ``project_theme`` UNDER ``survey_styling`` — survey keys win (shallow, top-level).

    - ``merge_styling({}, {})``                       → ``{}``                (unchanged default)
    - ``merge_styling(theme, {})``                    → ``theme``             (project fills all)
    - ``merge_styling({}, styling)``                  → ``styling``           (survey only)
    - ``merge_styling(theme, styling)``               → project keys, survey keys override

    The override is by TOP-LEVEL key: a survey ``background`` (a nested {type,value}) shadows the
    project ``background`` entirely — no deep-merge inside nested objects. ``None`` is treated as
    ``{}`` (a survey/workspace may carry null/empty). The inputs are never mutated."""
    project = project_theme or {}
    survey = survey_styling or {}
    return {**project, **survey}
