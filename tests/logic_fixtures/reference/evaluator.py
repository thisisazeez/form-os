"""Reference Python implementation of the OpenSurvey logic evaluator.

================================================================================
  REFERENCE / STAND-IN IMPLEMENTATION — NOT the production evaluator.
================================================================================
This module exists so the shared TS<->PY fixture harness is runnable BEFORE
Wave 3 ships the real evaluators (PLAN.md §6/§15, contracts/decisions.md
ADR-004). It encodes the *frozen* evaluation semantics documented in
``tests/logic_fixtures/README.md`` §2-§4 and in ``contracts/survey-schema.json``
``#/$defs/action`` ``$comment``.

Wave 3 replaces this with the real Django-side evaluator (e.g.
``apps.surveys.logic.evaluator``) exporting the SAME three functions with the
SAME behavior; the runner then imports that module instead (see README §6,
"Injecting the real evaluator"). The fixtures do not change.

The matching ``evaluator.ts`` in this directory is the line-for-line TS twin.
Keep them behaviorally identical — that is the whole point of the corpus.
"""

from __future__ import annotations

from typing import Any


# --------------------------------------------------------------------------- #
#  Return-shape helpers (NextStep)                                            #
# --------------------------------------------------------------------------- #
def _question(qid: str) -> dict[str, Any]:
    return {"type": "question", "id": qid}


def _ending(eid: str) -> dict[str, Any]:
    return {"type": "ending", "id": eid}


def _end() -> dict[str, Any]:
    # NOTE: "id" intentionally omitted (not None) for end-of-survey, per README §2.
    return {"type": "end"}


# --------------------------------------------------------------------------- #
#  Answer helpers                                                             #
# --------------------------------------------------------------------------- #
def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, list | tuple | dict):
        return len(value) == 0
    return False  # numbers (incl. 0) and booleans (incl. False) are NOT empty


def _is_submitted(answers: dict[str, Any], qid: str) -> bool:
    """A non-empty answer was actually given (README §3)."""
    return qid in answers and not _is_empty_value(answers[qid])


def _is_skipped(answers: dict[str, Any], qid: str) -> bool:
    """The question's key is absent (optional, not answered) (README §3)."""
    return qid not in answers


# --------------------------------------------------------------------------- #
#  Condition evaluation                                                       #
# --------------------------------------------------------------------------- #
def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _is_object_shape(value: Any) -> bool:
    """True if `value` is an object-shaped answer (a dict), e.g. contactInfo
    ({firstName?,...}) or matrix ({rowId: columnId|[columnId]}).

    FROZEN rule (survey-schema.json `operator` `$comment`; FU-1): the
    equality-family operators (equals / notEquals / isOneOf) operate on SCALARS
    or ARRAYS OF SCALARS only. An object-shaped answer is NOT a valid comparison
    target and deterministically does not match (equals->False, notEquals->True,
    isOneOf->False) — identically in TS and Python (no deep object compare). The
    TS twin's `deepEqual` already returns False for non-array objects; this guard
    makes Python agree EXPLICITLY rather than deep-comparing dicts.
    """
    return isinstance(value, dict)


def _eval_leaf(leaf: dict[str, Any], answers: dict[str, Any]) -> bool:
    qid = leaf["leftQuestionId"]
    op = leaf["operator"]
    expected = leaf.get("value")
    actual = answers.get(qid)

    # Presence operators (no value) ---------------------------------------- #
    if op == "isSubmitted":
        return _is_submitted(answers, qid)
    if op == "isSkipped":
        return _is_skipped(answers, qid)
    if op == "isEmpty":
        return qid not in answers or _is_empty_value(actual)
    if op == "isNotEmpty":
        return qid in answers and not _is_empty_value(actual)

    # consent (boolean) ---------------------------------------------------- #
    if op == "isAccepted":
        return actual is True
    if op == "isNotAccepted":
        return actual is not True
    # cta -> isClicked (boolean true)
    if op == "isClicked":
        return actual is True

    # If the question wasn't answered, value-comparisons are false.
    if qid not in answers:
        return False

    # Scalar equality ------------------------------------------------------ #
    # Object-shaped answers are NOT a valid equality-family target: they do not
    # match (equals->False, notEquals->True), per the operator `$comment` (FU-1).
    if op == "equals":
        if _is_object_shape(actual) or _is_object_shape(expected):
            return False
        return actual == expected
    if op == "notEquals":
        if _is_object_shape(actual) or _is_object_shape(expected):
            return True  # vacuously not-equal: object is not a valid target
        return actual != expected

    # String operators ----------------------------------------------------- #
    if op == "contains":
        return isinstance(actual, str) and isinstance(expected, str) and expected in actual
    if op == "doesNotContain":
        return not (isinstance(actual, str) and isinstance(expected, str) and expected in actual)
    if op == "startsWith":
        return isinstance(actual, str) and isinstance(expected, str) and actual.startswith(expected)
    if op == "endsWith":
        return isinstance(actual, str) and isinstance(expected, str) and actual.endswith(expected)

    # Numeric / ordered ---------------------------------------------------- #
    if op == "greaterThan":
        return _num(actual) > _num(expected)
    if op == "greaterThanOrEqual":
        return _num(actual) >= _num(expected)
    if op == "lessThan":
        return _num(actual) < _num(expected)
    if op == "lessThanOrEqual":
        return _num(actual) <= _num(expected)

    # Date (lexicographic ISO compare is order-correct for YYYY-MM-DD) ----- #
    if op == "before":
        return str(actual) < str(expected)
    if op == "after":
        return str(actual) > str(expected)

    # Single-select membership --------------------------------------------- #
    if op == "isOneOf":
        # An object-shaped answer is not a valid equality-family target -> no
        # match, per the operator `$comment` (FU-1). (The TS twin's deepEqual
        # likewise never matches a non-array object.)
        if _is_object_shape(actual):
            return False
        return actual in _as_list(expected)

    # Multi-select set operators ------------------------------------------- #
    if op in ("includesAll", "includesAny", "includesOnly", "doesNotInclude"):
        selected = set(_as_list(actual))
        target = set(_as_list(expected))
        if op == "includesAll":
            return target.issubset(selected)
        if op == "includesAny":
            return len(selected & target) > 0
        if op == "includesOnly":
            return selected == target
        if op == "doesNotInclude":
            return len(selected & target) == 0

    raise ValueError(f"Unknown operator: {op!r}")


def _num(value: Any) -> float:
    if isinstance(value, bool):  # guard: bool is an int subclass in Python
        raise ValueError("boolean is not a numeric operand")
    if isinstance(value, int | float):
        return float(value)
    raise ValueError(f"non-numeric operand: {value!r}")


def _eval_condition(node: dict[str, Any], answers: dict[str, Any]) -> bool:
    """Evaluate a conditionGroup: a leaf, or an and/or connector node."""
    if "connector" in node:
        conditions = node["conditions"]
        results = (_eval_condition(c, answers) for c in conditions)
        if node["connector"] == "and":
            return all(results)
        return any(results)  # "or"
    return _eval_leaf(node, answers)


# --------------------------------------------------------------------------- #
#  Survey indexing                                                            #
# --------------------------------------------------------------------------- #
def _question_index(survey: dict[str, Any]) -> dict[str, int]:
    return {q["id"]: i for i, q in enumerate(survey.get("questions", []))}


def _ending_ids(survey: dict[str, Any]) -> list[str]:
    return [e["id"] for e in survey.get("endings", [])]


def _first_matching_nav_action(question: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any] | None:
    """Return the action of the FIRST rule whose condition is true AND whose action
    is a navigation action (jumpTo/skip/endSurvey). 'require' is skipped (not
    navigation) per README §3 — we keep scanning past it.
    """
    for rule in question.get("logic", []):
        if not _eval_condition(rule["condition"], answers):
            continue
        action = rule["action"]
        if action["type"] in ("jumpTo", "skip", "endSurvey"):
            return action
        # 'require' (or any non-navigation action): not navigation; continue scanning.
    return None


def _skipped_question_ids(survey: dict[str, Any], answers: dict[str, Any]) -> set[str]:
    """Cumulative skip set (README §3, 'skip is cumulative'): for every ANSWERED
    question, if its first matching navigation rule is a 'skip', collect targets."""
    skipped: set[str] = set()
    for question in survey.get("questions", []):
        if question["id"] not in answers:
            continue
        action = _first_matching_nav_action(question, answers)
        if action is not None and action["type"] == "skip":
            target = action["target"]
            if isinstance(target, list):
                skipped.update(target)
            else:
                skipped.add(target)
    return skipped


def _resolve_end(survey: dict[str, Any]) -> dict[str, Any]:
    """Falling off the end resolves to the first ending if any, else {type:end}."""
    endings = _ending_ids(survey)
    if endings:
        return _ending(endings[0])
    return _end()


def _next_linear_after(
    survey: dict[str, Any],
    start_index: int,
    skipped: set[str],
) -> dict[str, Any]:
    """Advance forward from start_index (exclusive) to the next non-skipped question;
    fall off the end -> _resolve_end."""
    questions = survey.get("questions", [])
    for i in range(start_index + 1, len(questions)):
        qid = questions[i]["id"]
        if qid in skipped:
            continue
        return _question(qid)
    return _resolve_end(survey)


# --------------------------------------------------------------------------- #
#  PUBLIC CONTRACT: next_step / required_question_ids / validate_survey       #
# --------------------------------------------------------------------------- #
def next_step(
    survey: dict[str, Any],
    answers_so_far: dict[str, Any],
    current_question_id: str | None,
) -> dict[str, Any]:
    """Decide the next step. See README §2 for the frozen contract & evaluation order."""
    answers = answers_so_far or {}
    q_index = _question_index(survey)
    ending_ids = set(_ending_ids(survey))
    skipped = _skipped_question_ids(survey, answers)

    # Start of survey: first non-skipped question (or an ending if none). ---- #
    if current_question_id is None:
        return _next_linear_after(survey, -1, skipped)

    if current_question_id not in q_index:
        raise ValueError(f"currentQuestionId {current_question_id!r} is not a question in this survey")

    questions = survey["questions"]
    current = questions[q_index[current_question_id]]
    action = _first_matching_nav_action(current, answers)

    if action is not None:
        if action["type"] == "endSurvey":
            ending_id = action.get("endingId")
            if ending_id is not None:
                return _ending(ending_id)
            return _resolve_end(survey)

        if action["type"] == "jumpTo":
            target = action["target"]
            if target in ending_ids:
                return _ending(target)
            if target in q_index:
                # Explicit jump target is authoritative; we do not skip it even if
                # it appears in the skip set (README §3).
                return _question(target)
            raise ValueError(f"jumpTo target {target!r} is neither a question nor an ending")

        if action["type"] == "skip":
            # The skip already removed its target(s) from `skipped`; advance linearly
            # from the current question, stepping over skipped ones.
            return _next_linear_after(survey, q_index[current_question_id], skipped)

    # No navigation rule matched: advance to the next non-skipped question.
    return _next_linear_after(survey, q_index[current_question_id], skipped)


def required_question_ids(survey: dict[str, Any], answers_so_far: dict[str, Any]) -> set[str]:
    """Every statically-required question, PLUS every 'require' action target whose
    rule condition is true — but ONLY for rules whose HOST question has been
    answered.

    FROZEN interpretation (README §3): 'require' actions are "applied during
    answering" (survey-schema.json `#/$defs/action` `$comment`), i.e. a require
    rule on question Q only takes effect once Q itself is in `answersSoFar`. A
    require rule on a not-yet-answered host does NOT pre-emptively mark its target
    required. (Fixture: 'required — require on a not-yet-answered host does not
    apply'.)
    """
    answers = answers_so_far or {}
    required: set[str] = set()
    for question in survey.get("questions", []):
        if question.get("required", False):
            required.add(question["id"])
        # require rules apply only once their HOST question is answered.
        if question["id"] not in answers:
            continue
        for rule in question.get("logic", []):
            action = rule["action"]
            if action["type"] == "require" and _eval_condition(rule["condition"], answers):
                required.add(action["target"])
    return required


# --------------------------------------------------------------------------- #
#  validate_survey — the in-code checks JSON Schema cannot express            #
#  (README §4; ADR-004; REVIEW.md §2.3)                                        #
# --------------------------------------------------------------------------- #
# operator-validity table, frozen in survey-schema.json `operator` description.
_TEXT_OPS = {
    "equals", "notEquals", "contains", "doesNotContain", "startsWith", "endsWith",
    "isSubmitted", "isSkipped", "isEmpty", "isNotEmpty",
}
_SINGLE_OPS = {"equals", "notEquals", "isOneOf", "isSubmitted", "isSkipped"}
_MULTI_OPS = {"includesAll", "includesAny", "includesOnly", "doesNotInclude", "isSubmitted", "isSkipped"}
_NUMERIC_OPS = {
    "equals", "notEquals", "greaterThan", "greaterThanOrEqual", "lessThan",
    "lessThanOrEqual", "isOneOf", "isSubmitted", "isSkipped",
}
_DATE_OPS = {"equals", "notEquals", "before", "after", "isSubmitted", "isSkipped"}
_CONSENT_OPS = {"isAccepted", "isNotAccepted", "isSubmitted", "isSkipped"}
_CTA_OPS = {"isClicked", "isSkipped"}
_MATRIX_RANKING_OPS = {"isSubmitted", "isSkipped"}

_OPS_BY_TYPE: dict[str, set[str]] = {
    "openText": _TEXT_OPS,
    "contactInfo": _TEXT_OPS,
    "singleSelect": _SINGLE_OPS,
    "pictureChoice": _SINGLE_OPS | _MULTI_OPS,  # single or multi depending on allowMulti
    "multiSelect": _MULTI_OPS,
    "rating": _NUMERIC_OPS,
    "nps": _NUMERIC_OPS,
    "number": _NUMERIC_OPS,
    "date": _DATE_OPS,
    "consent": _CONSENT_OPS,
    "cta": _CTA_OPS,
    "matrix": _MATRIX_RANKING_OPS,
    "ranking": _MATRIX_RANKING_OPS,
    "hidden": _TEXT_OPS,
}


def _iter_leaves(node: dict[str, Any]):
    if "connector" in node:
        for c in node["conditions"]:
            yield from _iter_leaves(c)
    else:
        yield node


def validate_survey(survey: dict[str, Any]) -> dict[str, Any]:
    """Return {"ok": True} or {"ok": False, "errors": [{code, message, ...}]}.

    Checks (README §4): unique ids, no backward/unknown jumpTo (and require target),
    operator/type validity, condition refs at-or-before host & existing.
    """
    errors: list[dict[str, Any]] = []
    questions = survey.get("questions", [])
    q_index = {q["id"]: i for i, q in enumerate(questions)}
    q_type = {q["id"]: q["type"] for q in questions}
    ending_ids = set(_ending_ids(survey))

    # (1) unique ids across questions + endings -------------------------------
    seen: set[str] = set()
    for q in questions:
        if q["id"] in seen:
            errors.append({"code": "duplicate_id", "message": f"Duplicate id {q['id']!r}", "id": q["id"]})
        seen.add(q["id"])
    for e in survey.get("endings", []):
        if e["id"] in seen:
            errors.append({"code": "duplicate_id", "message": f"Duplicate id {e['id']!r}", "id": e["id"]})
        seen.add(e["id"])

    # (2) + (3) per-question logic checks -------------------------------------
    for host_idx, question in enumerate(questions):
        for rule in question.get("logic", []):
            action = rule["action"]

            # backward / unknown jumpTo & require targets
            if action["type"] in ("jumpTo", "require"):
                target = action["target"]
                if target in ending_ids:
                    pass  # endings are always "ahead"
                elif target not in q_index:
                    errors.append({
                        "code": "unknown_target",
                        "message": f"{action['type']} target {target!r} does not exist",
                        "ruleId": rule.get("id"),
                    })
                elif q_index[target] <= host_idx:
                    errors.append({
                        "code": "backward_jump",
                        "message": f"{action['type']} target {target!r} is not after the host question",
                        "ruleId": rule.get("id"),
                    })
            elif action["type"] == "skip":
                targets = action["target"]
                targets = targets if isinstance(targets, list) else [targets]
                for t in targets:
                    if t not in q_index:
                        errors.append({
                            "code": "unknown_target",
                            "message": f"skip target {t!r} does not exist",
                            "ruleId": rule.get("id"),
                        })
                    elif q_index[t] <= host_idx:
                        errors.append({
                            "code": "backward_jump",
                            "message": f"skip target {t!r} is not after the host question",
                            "ruleId": rule.get("id"),
                        })
            elif action["type"] == "endSurvey":
                ending_id = action.get("endingId")
                if ending_id is not None and ending_id not in ending_ids:
                    errors.append({
                        "code": "unknown_target",
                        "message": f"endSurvey endingId {ending_id!r} does not exist",
                        "ruleId": rule.get("id"),
                    })

            # operator/type validity + condition ref position
            for leaf in _iter_leaves(rule["condition"]):
                ref = leaf["leftQuestionId"]
                if ref not in q_index:
                    errors.append({
                        "code": "unknown_condition_ref",
                        "message": f"condition references unknown question {ref!r}",
                        "ruleId": rule.get("id"),
                    })
                    continue
                if q_index[ref] > host_idx:
                    errors.append({
                        "code": "forward_condition_ref",
                        "message": f"condition references a later question {ref!r}",
                        "ruleId": rule.get("id"),
                    })
                valid_ops = _OPS_BY_TYPE.get(q_type[ref], set())
                if leaf["operator"] not in valid_ops:
                    errors.append({
                        "code": "invalid_operator",
                        "message": f"operator {leaf['operator']!r} invalid for type {q_type[ref]!r}",
                        "ruleId": rule.get("id"),
                    })

    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True}
