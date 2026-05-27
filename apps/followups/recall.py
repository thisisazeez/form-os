"""
Python recall/piping resolver — the line-for-line twin of ``frontend/src/lib/recall.ts``.

Follow-up email subjects + bodies may embed recall tokens (an author piping a prior answer
or a captured hidden field into the message). The runtime/builder resolve recall in TS at
render time; the follow-up SEND path resolves them server-side against the FINAL Response.data
when it renders the email. This module is that server-side resolver.

DRIFT GUARD (the FU-1 lesson, contracts/survey-schema.json operator $comment): the TS and
Python halves of this platform have drifted before on object-shape handling. This file mirrors
``recall.ts`` EXACTLY — same regex, same ``_is_empty`` rule (None/""/[]/{}), same
``_render_value`` rule (scalars -> str, bool -> "Yes"/"No", arrays -> ", ".join of scalar
members, ``{"other": "..."}`` -> the typed text, other dicts -> None -> fallback). The shared
test fixture (tests/test_followups_recall.py) mirrors recall.test.ts so a divergence is caught.

FROZEN TOKEN FORMAT (survey-schema.json #/$defs/i18nString recall note):

    #recall:<id>/fallback:<text>#

  - <id>    an EARLIER question id, a hiddenFields[].id, OR a survey variables[].id (charset =
            #/$defs/questionId; all three share that id namespace). Variable LIVE values are
            threaded into ``answers`` by the caller (the runtime via computeVariables; this
            module's follow-up send path enriches Response.data before rendering) — exactly like
            hidden-field values, so resolve_recall reads them by id with no special case.
  - <text>  the fallback shown when the source is unanswered/empty; MAY be empty; MUST NOT
            contain '#' (the '#' delimiters bound the token).
"""
from __future__ import annotations

import re
from typing import Any

# The frozen recall token matcher (mirrors RECALL_TOKEN_RE in recall.ts). Capture groups:
#   [1] = source id (question id / hidden field id)
#   [2] = fallback text (may be empty; may not contain '#')
RECALL_TOKEN_RE = re.compile(r"#recall:([a-zA-Z][a-zA-Z0-9_-]{0,63})/fallback:([^#]*)#")

# A probe (the JS `RECALL_PROBE_RE`); identical pattern, used for the no-token fast path.
_RECALL_PROBE_RE = re.compile(r"#recall:[a-zA-Z][a-zA-Z0-9_-]{0,63}/fallback:[^#]*#")

__all__ = ["RECALL_TOKEN_RE", "has_recall_token", "resolve_recall"]


def has_recall_token(text: str | None) -> bool:
    """True if ``text`` contains at least one recall token (mirrors hasRecallToken)."""
    return isinstance(text, str) and bool(_RECALL_PROBE_RE.search(text))


def _is_empty(value: Any) -> bool:
    """True if an answer value is "empty" (mirrors recall.ts isEmpty + the engine rule).

    Empty == None, "", an empty list/tuple, or an empty dict. Numbers (incl. 0) and booleans
    (incl. False) are NOT empty.
    """
    if value is None or value == "":
        return True
    if isinstance(value, list | tuple | dict):
        return len(value) == 0
    return False


def _render_value(value: Any) -> str | None:
    """Render a single answer value to display text for piping (mirrors renderValue).

    Returns None when the value cannot be rendered as plain text (object-shaped answers that
    are not the {other:...} case) so the caller uses the fallback instead.
    """
    if value is None:
        return None
    # bool BEFORE int/float: bool is an int subclass in Python (guard it).
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        # [choiceId | url | ...] — join scalar members; drop non-scalars (mirrors recall.ts).
        parts = [
            str(v)
            for v in value
            if isinstance(v, str | int | float) and not isinstance(v, bool)
        ]
        return ", ".join(parts) if parts else None
    if isinstance(value, dict):
        # An "Other" choice answer carries the typed text; pipe that.
        other = value.get("other")
        if isinstance(other, str) and other != "":
            return other
        # contactInfo / address / matrix / scheduleMeeting object shapes are not piped (v1).
        return None
    return None


def resolve_recall(text: str | None, answers: dict[str, Any] | None) -> str:
    """Resolve recall tokens in ``text`` against ``answers`` (Response.data).

    ``answers`` includes captured hidden-field values (the runtime captures them into the
    response data under the same id). Text with NO token is returned UNCHANGED. Each token
    becomes its live rendered value, or its fallback when the source is unanswered/empty/
    unrenderable. Mirrors resolveRecall(text, answers) in recall.ts.
    """
    if not isinstance(text, str) or text == "":
        return text or ""
    if not _RECALL_PROBE_RE.search(text):
        return text  # no token -> unchanged (fast path)

    answer_map = answers or {}

    def _sub(match: re.Match[str]) -> str:
        source_id, fallback = match.group(1), match.group(2)
        value = answer_map.get(source_id)
        if _is_empty(value):
            return fallback
        rendered = _render_value(value)
        return rendered if rendered is not None and rendered != "" else fallback

    return RECALL_TOKEN_RE.sub(_sub, text)
