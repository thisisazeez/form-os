"""
In-code validation for survey response QUOTAS (beyond what jsonschema can express).

jsonschema (survey-schema.json #/$defs/quota) checks the STRUCTURE — id/name/limit/condition,
the conditionGroup boolean-tree shape. It cannot assert CROSS-REFERENCES (a quota's `endingId`
must resolve to a declared endings[].id; a condition's `leftQuestionId` must resolve to a real
question / hidden-field id with a type-valid operator) nor cross-item uniqueness of quota ids.
Those are checked here, mirroring how ``apps.followups.validation.validate_follow_ups`` adds
in-code follow-up cross-refs the schema can't.

Pure functions (no Django/model imports) so ``apps.surveys.validators`` can call this on the
write path without a circular import. Error dicts use the same ``{code, message, ...}`` shape
as the logic validator; codes are namespaced ``quota_*`` so the view surfaces them cleanly.

OPERATOR/TYPE VALIDITY: a quota condition is evaluated by the SAME frozen evaluator as logic
(apps.surveys.logic._eval_condition), so it must use the SAME operator/type table the logic
validator uses (apps.surveys.logic._OPS_BY_TYPE). We reuse that table directly (no rewrite) so
quota conditions and logic conditions share ONE source of truth for what operator is valid for a
question type. A quota condition is evaluated against the FINAL Response.data, so (unlike
per-question logic) there is no "forward reference" notion — any question OR hidden-field id is a
valid reference. Variable ids are NOT a valid quota-condition ref: quotas are evaluated against
Response.data ONLY (no variable replay), so a condition leaf referencing a variable id is
reported as an unknown ref (documented semantics — keeps quota counting simple + deterministic).
"""
from __future__ import annotations

from typing import Any

from .logic import _OPS_BY_TYPE


def _iter_condition_leaves(node: dict[str, Any]):
    """Yield every leaf of a conditionGroup (a leaf, or recurse into a connector)."""
    if not isinstance(node, dict):
        return
    if "connector" in node:
        for child in node.get("conditions", []) or []:
            yield from _iter_condition_leaves(child)
    else:
        yield node


def validate_quotas(survey: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the ``quotas`` array against the survey's ending + question/hidden-field namespaces.

    Returns a list of error dicts (empty == valid). Accepts the ``quotas`` key in either the
    snake_case model payload or the camelCase schema view (the key is the same in both — no
    rename), so it can run on the same payload the schema validator sees.

    Checks (frozen error codes):
      - ``quota_duplicate_id``          — two quotas share an id.
      - ``quota_invalid_limit``         — limit is not an integer >= 1 (defensive; the schema also
        constrains this, but in-code keeps a clean error if a malformed payload reaches here).
      - ``quota_unknown_ending``        — a quota's ``endingId`` does not resolve to a declared
        endings[].id.
      - ``quota_unknown_condition_ref`` — a condition leaf references a question/hidden-field id
        that does not exist in this survey.
      - ``quota_invalid_operator``      — a condition leaf's operator is not valid for the
        referenced question's type (reuses apps.surveys.logic._OPS_BY_TYPE — the SAME table the
        logic validator uses, so quota + logic conditions share one operator/type contract).
    """
    errors: list[dict[str, Any]] = []
    quotas = survey.get("quotas")
    if not isinstance(quotas, list):
        return errors  # structure errors are jsonschema's job; nothing to cross-check.

    # The valid condition-reference namespace: question ids + survey-level hidden-field ids, plus
    # a per-id type map for operator validity. (Endings are not answer sources; variables are not
    # part of Response.data — neither is a valid quota-condition ref.)
    questions = survey.get("questions") or []
    q_type: dict[str, str] = {
        q["id"]: q["type"]
        for q in questions
        if isinstance(q, dict) and "id" in q and "type" in q
    }
    ref_ids = set(q_type)
    hidden_fields = survey.get("hiddenFields")
    if hidden_fields is None:
        hidden_fields = survey.get("hidden_fields") or []
    hidden_ids = {
        hf["id"] for hf in hidden_fields if isinstance(hf, dict) and "id" in hf
    }
    ref_ids |= hidden_ids

    ending_ids = {
        e["id"]
        for e in (survey.get("endings") or [])
        if isinstance(e, dict) and "id" in e
    }

    seen: set[str] = set()
    for quota in quotas:
        if not isinstance(quota, dict):
            continue
        q_id = quota.get("id")
        if q_id is not None:
            if q_id in seen:
                errors.append({
                    "code": "quota_duplicate_id",
                    "message": f"Duplicate quota id {q_id!r}",
                    "id": q_id,
                })
            seen.add(q_id)

        limit = quota.get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            errors.append({
                "code": "quota_invalid_limit",
                "message": f"quota {q_id!r} limit must be an integer >= 1",
                "id": q_id,
            })

        ending_id = quota.get("endingId")
        if ending_id is not None and ending_id not in ending_ids:
            errors.append({
                "code": "quota_unknown_ending",
                "message": f"quota {q_id!r} endingId {ending_id!r} does not exist",
                "id": q_id,
            })

        condition = quota.get("condition")
        if isinstance(condition, dict):
            for leaf in _iter_condition_leaves(condition):
                if not isinstance(leaf, dict):
                    continue
                ref = leaf.get("leftQuestionId")
                if ref is None:
                    continue
                if ref not in ref_ids:
                    errors.append({
                        "code": "quota_unknown_condition_ref",
                        "message": (
                            f"quota {q_id!r} condition references unknown id {ref!r}"
                        ),
                        "id": q_id,
                    })
                    continue
                # Hidden-field refs have no question type; the evaluator treats them as text
                # operands, so accept the text-operator family for them (mirrors how logic refs
                # to hidden fields behave). For a real question, enforce the type-valid operator
                # set — the SAME table the logic validator uses.
                op = leaf.get("operator")
                if ref in q_type:
                    valid_ops = _OPS_BY_TYPE.get(q_type[ref], set())
                else:  # hidden-field ref
                    valid_ops = _OPS_BY_TYPE["hidden"]
                if op not in valid_ops:
                    errors.append({
                        "code": "quota_invalid_operator",
                        "message": (
                            f"quota {q_id!r} operator {op!r} invalid for reference {ref!r}"
                        ),
                        "id": q_id,
                    })

    return errors
