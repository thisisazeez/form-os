"""
In-code validation for survey follow-up DEFINITIONS (beyond what jsonschema can express).

jsonschema (survey-schema.json #/$defs/followUp) checks the STRUCTURE — trigger/action/
recipient discriminators, i18n subject/body, the recall-token charset. It cannot assert
CROSS-REFERENCES (a trigger condition's ``leftQuestionId`` or a ``responseField`` recipient's
``questionId`` must resolve to a real question/hidden-field id) nor cross-item uniqueness of
follow-up ids. Those are checked here, mirroring how ``apps.surveys.logic.validate_survey``
adds in-code logic checks the schema can't.

Pure functions (no Django/model imports) so ``apps.surveys.validators`` can call this on the
write path without a circular import. Error dicts use the same ``{code, message, ...}`` shape
as the logic validator; codes are namespaced ``followup_*`` so the view surfaces them cleanly.
"""
from __future__ import annotations

import re
from typing import Any

# A pragmatic email shape check (the schema declares format:email, but jsonschema does NOT
# enforce string formats by default and we deliberately don't enable a global format-checker —
# it would retroactively validate every uri/date-time field in the frozen contract). So we
# check the fixedEmail recipient's address here, in-code, where the blast radius is just
# follow-ups. Loose-but-useful: requires an '@' with non-empty local + dotted-domain parts.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _iter_condition_leaves(node: dict[str, Any]):
    """Yield every leaf of a conditionGroup (a leaf, or recurse into a connector)."""
    if not isinstance(node, dict):
        return
    if "connector" in node:
        for child in node.get("conditions", []) or []:
            yield from _iter_condition_leaves(child)
    else:
        yield node


def validate_follow_ups(survey: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the ``follow_ups`` / ``followUps`` array against the survey's id namespace.

    Returns a list of error dicts (empty == valid). Accepts either the snake_case model key
    (``follow_ups``) or the camelCase schema-view key (``followUps``) so it can run on the
    same payload the schema validator sees.

    Checks (frozen error codes):
      - ``followup_duplicate_id``        — two follow-ups share an id.
      - ``followup_unknown_condition_ref`` — a trigger condition references a question/hidden
        field id that does not exist in this survey.
      - ``followup_unknown_recipient_ref`` — a ``responseField`` recipient's ``questionId``
        does not exist in this survey.

    Note: a follow-up condition is evaluated against the FINAL Response.data, so (unlike
    per-question logic) any question OR hidden-field id is a valid reference — there is no
    "forward reference" notion (all answers are present at finalize time).
    """
    errors: list[dict[str, Any]] = []
    follow_ups = survey.get("followUps")
    if follow_ups is None:
        follow_ups = survey.get("follow_ups") or []

    if not isinstance(follow_ups, list):
        return errors  # structure errors are jsonschema's job; nothing to cross-check.

    # The valid reference namespace: question ids + survey-level hidden-field ids. (Endings are
    # not answer sources, so they are NOT valid condition/recipient refs.)
    questions = survey.get("questions") or []
    ref_ids = {
        q["id"]
        for q in questions
        if isinstance(q, dict) and "id" in q
    }
    hidden_fields = survey.get("hiddenFields")
    if hidden_fields is None:
        hidden_fields = survey.get("hidden_fields") or []
    ref_ids |= {
        hf["id"] for hf in hidden_fields if isinstance(hf, dict) and "id" in hf
    }

    seen: set[str] = set()
    for fu in follow_ups:
        if not isinstance(fu, dict):
            continue
        fu_id = fu.get("id")
        if fu_id is not None:
            if fu_id in seen:
                errors.append({
                    "code": "followup_duplicate_id",
                    "message": f"Duplicate follow-up id {fu_id!r}",
                    "id": fu_id,
                })
            seen.add(fu_id)

        trigger = fu.get("trigger") or {}
        condition = trigger.get("condition")
        if isinstance(condition, dict):
            for leaf in _iter_condition_leaves(condition):
                ref = leaf.get("leftQuestionId")
                if ref is not None and ref not in ref_ids:
                    errors.append({
                        "code": "followup_unknown_condition_ref",
                        "message": (
                            f"follow-up {fu_id!r} condition references unknown id {ref!r}"
                        ),
                        "id": fu_id,
                    })

        action = fu.get("action") or {}
        recipient = action.get("recipient") or {}
        rtype = recipient.get("type")
        if rtype == "responseField":
            qid = recipient.get("questionId")
            if qid is not None and qid not in ref_ids:
                errors.append({
                    "code": "followup_unknown_recipient_ref",
                    "message": (
                        f"follow-up {fu_id!r} recipient references unknown question {qid!r}"
                    ),
                    "id": fu_id,
                })
        elif rtype == "fixedEmail":
            email = recipient.get("email")
            if isinstance(email, str) and not _EMAIL_RE.match(email):
                errors.append({
                    "code": "followup_invalid_recipient_email",
                    "message": (
                        f"follow-up {fu_id!r} fixedEmail recipient {email!r} is not a valid email"
                    ),
                    "id": fu_id,
                })

    return errors
