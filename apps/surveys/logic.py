"""
Production Python logic evaluator for OpenSurvey surveys (Wave 2c).

This is the REAL Django-side evaluator that Wave 3 of the contract calls for
(``tests/logic_fixtures/README.md`` §6, "Injecting the real evaluator";
``contracts/decisions.md`` ADR-004). It exports the three frozen functions

  * ``next_step(survey, answers_so_far, current_question_id)`` — navigation,
  * ``required_question_ids(survey, answers_so_far)``           — required set,
  * ``validate_survey(survey)``                                 — in-code checks,

and is BEHAVIORALLY IDENTICAL to the reference stand-in in
``tests/logic_fixtures/reference/evaluator.py``. The 64-case shared TS<->PY
fixture corpus is the verifier: point the harness at this module via

  OPENSURVEY_LOGIC_EVALUATOR=apps.surveys.logic \
      python -m pytest tests/logic_fixtures/run_py.py -v

and every case must pass against THIS implementation (not just the reference).

The semantics are frozen verbatim from ``contracts/survey-schema.json``
``#/$defs/action`` ``$comment`` and ``tests/logic_fixtures/README.md`` §2-§4.
Three subtle rules are individually pinned by fixtures and MUST NOT drift:

1. **FU-1 object-shape no-match** (``survey-schema.json`` ``operator`` ``$comment``):
   the equality-family operators (``equals`` / ``notEquals`` / ``isOneOf``) operate
   on SCALARS or ARRAYS OF SCALARS only. An OBJECT-SHAPED answer (contactInfo
   ``{firstName?,...}``, matrix ``{rowId: columnId|[columnId]}``) is NOT a valid
   comparison target and deterministically does not match:
   ``equals -> False``, ``notEquals -> True`` (vacuously not-equal), ``isOneOf -> False``.
   Identical in TS (whose ``deepEqual`` never matches a non-array object) and here.
2. **``require`` applies during answering** (README §3): a ``require`` rule on
   question ``Q`` takes effect ONLY once ``Q`` itself is in ``answers_so_far``.
3. **``skip`` is cumulative** (README §3): recomputed every call from every
   ANSWERED question's first-matching navigation rule.

The matching ``evaluator.ts`` (runtime + builder) is the line-for-line TS twin;
this module and that one must stay behaviorally identical — that is the whole
point of the corpus.
"""
from __future__ import annotations

from typing import Any


# --------------------------------------------------------------------------- #
#  Return-shape helpers (NextStep — README §2, FROZEN)                         #
#                                                                              #
#  { "type": "question", "id": "q5" }  -> show question q5 next                #
#  { "type": "ending",   "id": "e1" }  -> show ending screen e1                #
#  { "type": "end" }                   -> end of survey, no explicit ending    #
#  "id" is OMITTED (not None) for end-of-survey.                               #
# --------------------------------------------------------------------------- #
def _question(qid: str) -> dict[str, Any]:
    return {"type": "question", "id": qid}


def _ending(eid: str) -> dict[str, Any]:
    return {"type": "ending", "id": eid}


def _end() -> dict[str, Any]:
    # "id" intentionally omitted (not None) for end-of-survey, per README §2.
    return {"type": "end"}


# --------------------------------------------------------------------------- #
#  Answer helpers (README §3)                                                  #
# --------------------------------------------------------------------------- #
def _is_empty_value(value: Any) -> bool:
    """Empty == None, "", or an empty list/tuple/dict. Numbers (incl. 0) and
    booleans (incl. False) are NOT empty (README §3 isEmpty/isNotEmpty)."""
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, list | tuple | dict):
        return len(value) == 0
    return False


def _is_submitted(answers: dict[str, Any], qid: str) -> bool:
    """A non-empty answer was actually given (README §3)."""
    return qid in answers and not _is_empty_value(answers[qid])


def _is_skipped(answers: dict[str, Any], qid: str) -> bool:
    """The question's key is absent (optional, not answered) (README §3)."""
    return qid not in answers


# --------------------------------------------------------------------------- #
#  Condition evaluation                                                        #
# --------------------------------------------------------------------------- #
def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _is_object_shape(value: Any) -> bool:
    """True if ``value`` is an object-shaped answer (a dict): contactInfo
    ``{firstName?,...}`` or matrix ``{rowId: columnId|[columnId]}``.

    FROZEN rule (survey-schema.json ``operator`` ``$comment``; FU-1): the
    equality-family operators operate on scalars/arrays-of-scalars only. An
    object-shaped answer is NOT a valid comparison target and deterministically
    does not match (equals->False, notEquals->True, isOneOf->False) — identically
    in TS and Python (no deep object compare).
    """
    return isinstance(value, dict)


def _num(value: Any) -> float:
    if isinstance(value, bool):  # bool is an int subclass in Python — guard it.
        raise ValueError("boolean is not a numeric operand")
    if isinstance(value, int | float):
        return float(value)
    raise ValueError(f"non-numeric operand: {value!r}")


def _eval_leaf(leaf: dict[str, Any], answers: dict[str, Any]) -> bool:
    qid = leaf["leftQuestionId"]
    op = leaf["operator"]
    expected = leaf.get("value")
    actual = answers.get(qid)

    # Presence operators (take no value) ----------------------------------- #
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

    # For value-comparisons, an unanswered question is False.
    if qid not in answers:
        return False

    # Scalar equality. Object-shaped answers are NOT a valid equality-family
    # target: equals->False, notEquals->True (vacuously not-equal) — FU-1.
    if op == "equals":
        if _is_object_shape(actual) or _is_object_shape(expected):
            return False
        return actual == expected
    if op == "notEquals":
        if _is_object_shape(actual) or _is_object_shape(expected):
            return True
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

    # Date (lexicographic ISO compare is order-correct for YYYY-MM-DD) ------ #
    if op == "before":
        return str(actual) < str(expected)
    if op == "after":
        return str(actual) > str(expected)

    # Single-select membership --------------------------------------------- #
    if op == "isOneOf":
        # Object-shaped answer is not a valid equality-family target -> no match (FU-1).
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
#  Survey variables + the `calculate` action (Formbricks parity)              #
#                                                                              #
#  ADDITIVE + backward-compatible. When a survey has NO variables AND NO       #
#  calculate actions, the fast-path guard (`_survey_has_variables`) is False   #
#  and every public entry point runs the EXACT pre-existing code path on the   #
#  ORIGINAL answers dict — byte-identical to today (the frozen 64-case corpus  #
#  proves this stays green unchanged).                                          #
#                                                                              #
#  REPLAY SEMANTICS (frozen; identical in TS `frontend/src/lib/logic`):        #
#    compute_variables(survey, answers):                                        #
#      vars = seed from survey.variables[].initialValue                         #
#             (default 0 for number, "" for text)                               #
#      for question in survey.questions (ARRAY ORDER):                          #
#        if question.id not in answers: continue                                #
#        for rule in question.logic (ARRAY ORDER):                              #
#          enriched = {**answers, **vars}   # vars visible to conditions        #
#          if not eval_condition(rule.condition, enriched): continue            #
#          if action.type == "calculate": apply(vars, action, enriched);        #
#                                          continue   # NON-navigation          #
#          if action.type in (jumpTo|skip|endSurvey): break  # nav HALTS scan   #
#          # require: keep scanning                                             #
#      return vars                                                              #
#                                                                              #
#  Two pinned semantic choices (mirroring require/skip): (a) calculate applies  #
#  in array order WITHIN a host and its mutation is visible to subsequent rules #
#  on the same host; (b) a matching NAVIGATION rule halts the variable replay   #
#  for that host (a calculate AFTER a matched nav rule does not apply).         #
#                                                                              #
#  DETERMINISM GUARDS (explicit, not emergent — must match TS):                #
#    * divide-by-zero            -> no-op (variable unchanged)                  #
#    * non-numeric numeric operand -> no-op                                     #
#    * unresolvable/empty ref    -> no-op                                       #
#    * text concat of a non-string operand -> recall's render rule (scalars     #
#      stringify; bool -> "Yes"/"No"; object-shaped -> no-op)                   #
# --------------------------------------------------------------------------- #
_NUMERIC_CALC_OPS = {"assign", "add", "subtract", "multiply", "divide"}
_TEXT_CALC_OPS = {"assign", "concat"}


def _variable_defs(survey: dict[str, Any]) -> list[dict[str, Any]]:
    """The declared variables list (snake==camel: the key is `variables` either way)."""
    vs = survey.get("variables")
    return vs if isinstance(vs, list) else []


def _question_has_calculate(survey: dict[str, Any]) -> bool:
    for question in survey.get("questions", []):
        for rule in question.get("logic", []) or []:
            action = rule.get("action") or {}
            if action.get("type") == "calculate":
                return True
    return False


def _survey_has_variables(survey: dict[str, Any]) -> bool:
    """Fast-path guard: True iff this survey defines any variable OR any calculate
    action. When False, the evaluator MUST run the exact pre-variable code path on the
    original answers dict (byte-identical to today — the frozen corpus guarantee)."""
    if _variable_defs(survey):
        return True
    return _question_has_calculate(survey)


def _render_scalar_for_text(value: Any) -> str | None:
    """Render an operand to display text for a text `concat`/`assign` (mirrors the recall
    `_render_value` rule so calculate + recall stringify operands identically). Returns
    None for object-shaped / unrenderable operands (caller treats as a no-op)."""
    if value is None:
        return None
    if isinstance(value, bool):  # bool BEFORE int/float (bool is an int subclass)
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        parts = [
            str(v)
            for v in value
            if isinstance(v, str | int | float) and not isinstance(v, bool)
        ]
        return ", ".join(parts) if parts else None
    return None  # dict / other -> not renderable


def _resolve_calc_operand(
    value_spec: dict[str, Any] | None, enriched: dict[str, Any]
) -> Any:
    """Resolve a #/$defs/calcValue operand. literal -> its value; ref -> the value of
    that id in the enriched answer/variable map (None if absent). A malformed spec -> None."""
    if not isinstance(value_spec, dict):
        return None
    if value_spec.get("type") == "literal":
        return value_spec.get("value")
    if value_spec.get("type") == "ref":
        return enriched.get(value_spec.get("ref"))
    return None


def _try_num(value: Any) -> float | None:
    """Coerce to float for a numeric calc, or None if not a finite number (bool/str/etc.)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _normalize_number(n: float) -> int | float:
    """Normalize a computed number to match JavaScript's UNIFIED number semantics, so the
    TS + Python variable maps + their string renderings are byte-identical: a whole-valued
    float becomes an int (``15.0`` -> ``15``), so ``str(15)`` == JS ``String(15)`` == "15"
    (Python's ``str(15.0)`` would be "15.0", which JS never produces). Non-whole values are
    left as float (``15.5`` -> ``15.5`` == JS "15.5")."""
    if isinstance(n, float) and n.is_integer():
        return int(n)
    return n


def _apply_calculate(
    variables: dict[str, Any],
    var_types: dict[str, str],
    action: dict[str, Any],
    enriched: dict[str, Any],
) -> None:
    """Apply ONE calculate action to ``variables`` in place. A type-incompatible op, an
    unresolvable/empty operand, a non-numeric numeric operand, or a divide-by-zero is a
    deterministic NO-OP (the variable is left unchanged). Identical to the TS twin."""
    var_id = action.get("variableId")
    op = action.get("operation")
    if var_id is None or var_id not in var_types:
        return  # unknown variable -> no-op
    vtype = var_types[var_id]
    operand = _resolve_calc_operand(action.get("value"), enriched)

    if vtype == "number":
        if op not in _NUMERIC_CALC_OPS:
            return
        if op == "assign":
            n = _try_num(operand)
            if n is None:
                return
            variables[var_id] = _normalize_number(n)
            return
        operand_n = _try_num(operand)
        if operand_n is None:
            return  # non-numeric operand -> no-op
        current = _try_num(variables.get(var_id))
        if current is None:
            current = 0.0
        if op == "add":
            variables[var_id] = _normalize_number(current + operand_n)
        elif op == "subtract":
            variables[var_id] = _normalize_number(current - operand_n)
        elif op == "multiply":
            variables[var_id] = _normalize_number(current * operand_n)
        elif op == "divide":
            if operand_n == 0:
                return  # divide-by-zero -> no-op
            variables[var_id] = _normalize_number(current / operand_n)
        return

    if vtype == "text":
        if op not in _TEXT_CALC_OPS:
            return
        rendered = _render_scalar_for_text(operand)
        if rendered is None:
            return  # unrenderable operand -> no-op
        if op == "assign":
            variables[var_id] = rendered
        elif op == "concat":
            current = variables.get(var_id)
            current_str = current if isinstance(current, str) else ""
            variables[var_id] = current_str + rendered
        return


def compute_variables(
    survey: dict[str, Any], answers: dict[str, Any] | None
) -> dict[str, Any]:
    """Deterministically replay the survey's `calculate` actions over the answered path and
    return the live ``{variableId: value}`` map. PURE + stateless (recomputed from
    ``answers`` alone, like the skip set). See the module's REPLAY SEMANTICS block.

    Public helper (also called from the runtime preview path + the follow-up send path) so
    recall + conditions can read variable values. When the survey has no variables, returns
    ``{}`` (the caller then enriches with nothing — byte-identical to today)."""
    defs = _variable_defs(survey)
    if not defs:
        return {}
    answers = answers or {}

    variables: dict[str, Any] = {}
    var_types: dict[str, str] = {}
    for v in defs:
        if not isinstance(v, dict) or "id" not in v:
            continue
        vid = v["id"]
        vtype = v.get("type", "text")
        var_types[vid] = vtype
        if "initialValue" in v:
            init = v["initialValue"]
        else:
            init = 0 if vtype == "number" else ""
        # Seed numbers via _normalize_number so a whole value renders like JS ("0" not "0.0").
        if vtype == "number":
            n = _try_num(init)
            variables[vid] = _normalize_number(n if n is not None else 0.0)
        else:
            variables[vid] = init if isinstance(init, str) else ""

    for question in survey.get("questions", []):
        if question["id"] not in answers:
            continue
        for rule in question.get("logic", []) or []:
            enriched = {**answers, **variables}
            if not _eval_condition(rule["condition"], enriched):
                continue
            action = rule.get("action") or {}
            atype = action.get("type")
            if atype == "calculate":
                _apply_calculate(variables, var_types, action, enriched)
                continue  # non-navigation; keep scanning
            if atype in ("jumpTo", "skip", "endSurvey"):
                break  # a matching navigation rule halts the replay for this host
            # 'require' (or any other non-navigation action): keep scanning.
    return variables


def _enriched_answers(survey: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """The answers map a variables-aware survey threads through the evaluator: the raw
    answers OVERLAID with the live variable values (so conditions can compare a variable).
    Variable ids never collide with question/hidden ids (validate_survey's duplicate_id
    check guarantees it), so the overlay is unambiguous. Returns the SAME dict reference
    when the survey has no variables (the fast-path guard makes this branch unreachable for
    no-variable surveys, but we keep the helper total)."""
    variables = compute_variables(survey, answers)
    if not variables:
        return answers
    return {**answers, **variables}


# --------------------------------------------------------------------------- #
#  Survey indexing                                                            #
# --------------------------------------------------------------------------- #
def _question_index(survey: dict[str, Any]) -> dict[str, int]:
    return {q["id"]: i for i, q in enumerate(survey.get("questions", []))}


def _ending_ids(survey: dict[str, Any]) -> list[str]:
    return [e["id"] for e in survey.get("endings", [])]


def _first_matching_nav_action(
    question: dict[str, Any], answers: dict[str, Any]
) -> dict[str, Any] | None:
    """The action of the FIRST rule whose condition is true AND whose action is a
    navigation action (jumpTo/skip/endSurvey). ``require`` is NOT navigation
    (README §3) — keep scanning past it.
    """
    for rule in question.get("logic", []):
        if not _eval_condition(rule["condition"], answers):
            continue
        action = rule["action"]
        if action["type"] in ("jumpTo", "skip", "endSurvey"):
            return action
        # 'require' (or any non-navigation action): continue scanning.
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
    """Falling off the end resolves to the FIRST ending if any, else {type:end}
    (README §2, the frozen 'fall off the end' resolution rule)."""
    endings = _ending_ids(survey)
    if endings:
        return _ending(endings[0])
    return _end()


def _next_linear_after(
    survey: dict[str, Any], start_index: int, skipped: set[str]
) -> dict[str, Any]:
    """Advance forward from ``start_index`` (exclusive) to the next non-skipped
    question; fall off the end -> ``_resolve_end``."""
    questions = survey.get("questions", [])
    for i in range(start_index + 1, len(questions)):
        qid = questions[i]["id"]
        if qid in skipped:
            continue
        return _question(qid)
    return _resolve_end(survey)


# --------------------------------------------------------------------------- #
#  PUBLIC CONTRACT: next_step / required_question_ids / validate_survey        #
# --------------------------------------------------------------------------- #
def next_step(
    survey: dict[str, Any],
    answers_so_far: dict[str, Any] | None,
    current_question_id: str | None,
) -> dict[str, Any]:
    """Decide the next step. See README §2 for the frozen contract & evaluation
    order. The evaluator assumes a VALIDATED survey (no backward jumps).

    VARIABLES (additive): when the survey defines variables / calculate actions, the
    answers map is enriched with the live variable values (compute_variables) so a
    condition can compare a variable. The fast-path guard keeps a no-variable survey on
    the EXACT pre-variable path with the original answers dict — byte-identical to today
    (the frozen corpus proves it)."""
    answers = answers_so_far or {}
    if _survey_has_variables(survey):
        answers = _enriched_answers(survey, answers)
    q_index = _question_index(survey)
    ending_ids = set(_ending_ids(survey))
    skipped = _skipped_question_ids(survey, answers)

    # Start of survey: first non-skipped question (or an ending if none).
    if current_question_id is None:
        return _next_linear_after(survey, -1, skipped)

    if current_question_id not in q_index:
        raise ValueError(
            f"currentQuestionId {current_question_id!r} is not a question in this survey"
        )

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
                # Explicit jump target is authoritative; do not skip it even if it
                # appears in the skip set (README §3).
                return _question(target)
            raise ValueError(
                f"jumpTo target {target!r} is neither a question nor an ending"
            )

        if action["type"] == "skip":
            # The skip already removed its target(s) from ``skipped``; advance
            # linearly from the current question, stepping over skipped ones.
            return _next_linear_after(survey, q_index[current_question_id], skipped)

    # No navigation rule matched: advance to the next non-skipped question.
    return _next_linear_after(survey, q_index[current_question_id], skipped)


def required_question_ids(
    survey: dict[str, Any], answers_so_far: dict[str, Any] | None
) -> set[str]:
    """Every statically-required question, PLUS every ``require`` action target
    whose rule condition is true — but ONLY for rules whose HOST question has been
    answered.

    FROZEN interpretation (README §3): ``require`` actions are "applied during
    answering" (survey-schema.json ``#/$defs/action`` ``$comment``), i.e. a
    require rule on question ``Q`` only takes effect once ``Q`` itself is in
    ``answers_so_far``. A require rule on a not-yet-answered host does NOT
    pre-emptively mark its target required.

    VARIABLES (additive): enriched with live variable values when the survey uses them, so a
    require rule whose condition compares a variable resolves correctly. No-variable surveys
    keep the original answers dict (byte-identical to today).
    """
    answers = answers_so_far or {}
    if _survey_has_variables(survey):
        answers = _enriched_answers(survey, answers)
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
#  validate_survey — the in-code checks JSON Schema cannot express             #
#  (README §4; ADR-004; REVIEW.md §2.3)                                        #
# --------------------------------------------------------------------------- #
# Operator-validity table, frozen in survey-schema.json ``operator`` description.
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
    "address": _TEXT_OPS,  # object-shaped like contactInfo; FU-1 no-match still applies
    "singleSelect": _SINGLE_OPS,
    "pictureChoice": _SINGLE_OPS | _MULTI_OPS,  # single or multi by allowMulti
    "multiSelect": _MULTI_OPS,
    "rating": _NUMERIC_OPS,
    "nps": _NUMERIC_OPS,
    "number": _NUMERIC_OPS,
    "date": _DATE_OPS,
    "consent": _CONSENT_OPS,
    "cta": _CTA_OPS,
    "matrix": _MATRIX_RANKING_OPS,
    "ranking": _MATRIX_RANKING_OPS,
    "scheduleMeeting": _MATRIX_RANKING_OPS,  # booking object is opaque -> submitted/skipped only
    "hidden": _TEXT_OPS,
}


def _iter_leaves(node: dict[str, Any]):
    if "connector" in node:
        for c in node["conditions"]:
            yield from _iter_leaves(c)
    else:
        yield node


def validate_survey(survey: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"ok": True}`` or ``{"ok": False, "errors": [{code, message, ...}]}``.

    Checks (README §4): unique ids; no backward/unknown jumpTo (and require
    target); operator/type validity; condition refs at-or-before host & existing.

    Error codes (frozen): ``duplicate_id``, ``backward_jump``, ``unknown_target``,
    ``invalid_operator``, ``forward_condition_ref``, ``unknown_condition_ref``.

    VARIABLES (additive): variable ids join the shared duplicate_id namespace; a condition
    leaf MAY reference a variable id (its type drives operator validity: number->numeric ops,
    text->text ops) and is exempt from forward_condition_ref (variables have no linear
    position). The new ``calculate`` action is validated for: ``variableId`` resolves
    (``unknown_variable``), ``operation`` valid for the variable type (``invalid_calc_op``),
    and a ``value`` ref resolves to a known id (``unknown_calc_ref``).
    """
    errors: list[dict[str, Any]] = []
    questions = survey.get("questions", [])
    q_index = {q["id"]: i for i, q in enumerate(questions)}
    q_type = {q["id"]: q["type"] for q in questions}
    ending_ids = set(_ending_ids(survey))

    # Variable defs: {id: "number"|"text"}. A variable id is a valid condition ref + recall
    # source + calculate target; it joins the duplicate_id namespace below.
    variable_types: dict[str, str] = {}
    for v in _variable_defs(survey):
        if isinstance(v, dict) and "id" in v:
            variable_types[v["id"]] = v.get("type", "text")
    # The full set of ids that resolve as a value source (questions + hidden fields +
    # variables) — used by calculate `value` ref validation.
    _hidden_for_refs = survey.get("hiddenFields") or survey.get("hidden_fields") or []
    value_source_ids = set(q_index)
    value_source_ids |= {
        hf["id"] for hf in _hidden_for_refs if isinstance(hf, dict) and "id" in hf
    }
    value_source_ids |= set(variable_types)

    # (1) unique ids across questions + endings -----------------------------
    seen: set[str] = set()
    for q in questions:
        if q["id"] in seen:
            errors.append({"code": "duplicate_id", "message": f"Duplicate id {q['id']!r}", "id": q["id"]})
        seen.add(q["id"])
    for e in survey.get("endings", []):
        if e["id"] in seen:
            errors.append({"code": "duplicate_id", "message": f"Duplicate id {e['id']!r}", "id": e["id"]})
        seen.add(e["id"])
    # Hidden-field ids share the recall/Response.data namespace with question ids, so a
    # collision with a question/ending id (or another hidden field) is a duplicate_id.
    # The Python validator is called with the snake_case model payload (hidden_fields)
    # AND with the camelCase schema view (hiddenFields); accept either key.
    hidden_fields = survey.get("hiddenFields") or survey.get("hidden_fields") or []
    for hf in hidden_fields:
        if not isinstance(hf, dict) or "id" not in hf:
            continue
        if hf["id"] in seen:
            errors.append(
                {"code": "duplicate_id", "message": f"Duplicate id {hf['id']!r}", "id": hf["id"]}
            )
        seen.add(hf["id"])
    # Variable ids share the recall/condition id namespace too, so a collision with a
    # question/ending/hidden-field id (or another variable) is a duplicate_id.
    for v in _variable_defs(survey):
        if not isinstance(v, dict) or "id" not in v:
            continue
        if v["id"] in seen:
            errors.append(
                {"code": "duplicate_id", "message": f"Duplicate id {v['id']!r}", "id": v["id"]}
            )
        seen.add(v["id"])

    # (2) + (3) per-question logic checks -----------------------------------
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
            elif action["type"] == "calculate":
                # calculate is non-navigation: no backward/forward position constraint.
                var_id = action.get("variableId")
                if var_id not in variable_types:
                    errors.append({
                        "code": "unknown_variable",
                        "message": f"calculate variableId {var_id!r} is not a declared variable",
                        "ruleId": rule.get("id"),
                    })
                else:
                    op = action.get("operation")
                    vtype = variable_types[var_id]
                    valid_calc_ops = (
                        _NUMERIC_CALC_OPS if vtype == "number" else _TEXT_CALC_OPS
                    )
                    if op not in valid_calc_ops:
                        errors.append({
                            "code": "invalid_calc_op",
                            "message": (
                                f"operation {op!r} invalid for {vtype} variable {var_id!r}"
                            ),
                            "ruleId": rule.get("id"),
                        })
                # A `value` REF must resolve to a known value source (question / variable /
                # hidden field). A literal carries its own value (nothing to resolve).
                value_spec = action.get("value")
                if isinstance(value_spec, dict) and value_spec.get("type") == "ref":
                    ref = value_spec.get("ref")
                    if ref not in value_source_ids:
                        errors.append({
                            "code": "unknown_calc_ref",
                            "message": f"calculate value ref {ref!r} does not exist",
                            "ruleId": rule.get("id"),
                        })

            # operator/type validity + condition ref position
            for leaf in _iter_leaves(rule["condition"]):
                ref = leaf["leftQuestionId"]
                # A condition leaf may reference a VARIABLE id (additive): a variable has no
                # linear position (it is always available), so it is exempt from the
                # forward_condition_ref check; its operator validity is driven by its type
                # (number -> numeric ops, text -> text ops), reusing the existing op sets.
                if ref in variable_types:
                    vtype = variable_types[ref]
                    valid_ops = _NUMERIC_OPS if vtype == "number" else _TEXT_OPS
                    if leaf["operator"] not in valid_ops:
                        errors.append({
                            "code": "invalid_operator",
                            "message": (
                                f"operator {leaf['operator']!r} invalid for {vtype} variable {ref!r}"
                            ),
                            "ruleId": rule.get("id"),
                        })
                    continue
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
