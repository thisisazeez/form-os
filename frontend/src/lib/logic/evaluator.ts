// ============================================================================
// OpenSurvey — Production TypeScript logic engine.
//
// Implements `nextStep` / `requiredQuestionIds` / `validateSurvey` per the FROZEN
// evaluator contract (tests/logic_fixtures/README.md §2–§4 and
// contracts/survey-schema.json `#/$defs/action` `$comment`).
//
// This is the REAL Wave-3 evaluator. It is behaviorally identical to the
// reference stand-in (tests/logic_fixtures/reference/evaluator.ts) — the same
// 64-case shared fixture corpus runs against it via the documented TS injection
// shim (tests/logic_fixtures/evaluator-under-test.ts), which re-exports these
// three functions. The Python finalize validator is the line-for-line twin.
//
// Internally the engine operates on a loose `Json` view of the survey/answers:
// the schema deliberately leaves question objects open (forward-compat) and
// answer shapes are a union; a loose internal view keeps the algorithm an exact
// port of the frozen reference and avoids type-narrowing drift. The PUBLIC
// surface is fully typed (Survey / AnswerMap / NextStep).
// ============================================================================

import type { AnswerMap, Json, NextStep, Survey } from "../types";

export type { NextStep } from "../types";

// --------------------------------------------------------------------------- //
//  Return-shape helpers                                                       //
// --------------------------------------------------------------------------- //
const question = (id: string): NextStep => ({ type: "question", id });
const ending = (id: string): NextStep => ({ type: "ending", id });
const end = (): NextStep => ({ type: "end" }); // "id" intentionally omitted (README §2)

// --------------------------------------------------------------------------- //
//  Answer helpers                                                             //
// --------------------------------------------------------------------------- //
function isEmptyValue(value: Json): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value === "";
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false; // numbers (incl. 0) and booleans (incl. false) are NOT empty
}

function has(answers: Record<string, Json>, qid: string): boolean {
  return Object.prototype.hasOwnProperty.call(answers, qid);
}

function isSubmitted(answers: Record<string, Json>, qid: string): boolean {
  return has(answers, qid) && !isEmptyValue(answers[qid]);
}

function isSkipped(answers: Record<string, Json>, qid: string): boolean {
  return !has(answers, qid);
}

// --------------------------------------------------------------------------- //
//  Condition evaluation                                                       //
// --------------------------------------------------------------------------- //
function asList(value: Json): Json[] {
  return Array.isArray(value) ? value.slice() : [value];
}

/**
 * True if `value` is an object-shaped answer (a non-null, non-array object),
 * e.g. contactInfo ({firstName?,...}) or matrix ({rowId: columnId|[columnId]}).
 *
 * FROZEN rule (survey-schema.json `operator` `$comment`; FU-1): the
 * equality-family operators (equals / notEquals / isOneOf) operate on SCALARS or
 * ARRAYS OF SCALARS only. An object-shaped answer is NOT a valid comparison
 * target and deterministically does not match (equals->false, notEquals->true,
 * isOneOf->false) — identically in TS and Python. We guard EXPLICITLY so the
 * rule is stated in the file (not emergent) and the Python twin matches.
 */
function isObjectShape(value: Json): boolean {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function num(value: Json): number {
  if (typeof value === "boolean") throw new Error("boolean is not a numeric operand");
  if (typeof value === "number") return value;
  throw new Error(`non-numeric operand: ${JSON.stringify(value)}`);
}

function deepEqual(a: Json, b: Json): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => deepEqual(x, b[i]));
  }
  return false;
}

function evalLeaf(leaf: Json, answers: Record<string, Json>): boolean {
  const qid: string = leaf.leftQuestionId;
  const op: string = leaf.operator;
  const expected: Json = leaf.value;
  const actual: Json = answers[qid];

  // Presence operators (no value)
  if (op === "isSubmitted") return isSubmitted(answers, qid);
  if (op === "isSkipped") return isSkipped(answers, qid);
  if (op === "isEmpty") return !has(answers, qid) || isEmptyValue(actual);
  if (op === "isNotEmpty") return has(answers, qid) && !isEmptyValue(actual);

  // consent (boolean)
  if (op === "isAccepted") return actual === true;
  if (op === "isNotAccepted") return actual !== true;
  // cta
  if (op === "isClicked") return actual === true;

  // If the question wasn't answered, value comparisons are false.
  if (!has(answers, qid)) return false;

  // Scalar equality.
  // Object-shaped answers are NOT a valid equality-family target: they do not
  // match (equals->false, notEquals->true), per the operator `$comment` (FU-1).
  if (op === "equals") {
    if (isObjectShape(actual) || isObjectShape(expected)) return false;
    return deepEqual(actual, expected);
  }
  if (op === "notEquals") {
    if (isObjectShape(actual) || isObjectShape(expected)) return true; // vacuously not-equal
    return !deepEqual(actual, expected);
  }

  // String operators
  if (op === "contains")
    return typeof actual === "string" && typeof expected === "string" && actual.includes(expected);
  if (op === "doesNotContain")
    return !(typeof actual === "string" && typeof expected === "string" && actual.includes(expected));
  if (op === "startsWith")
    return typeof actual === "string" && typeof expected === "string" && actual.startsWith(expected);
  if (op === "endsWith")
    return typeof actual === "string" && typeof expected === "string" && actual.endsWith(expected);

  // Numeric / ordered
  if (op === "greaterThan") return num(actual) > num(expected);
  if (op === "greaterThanOrEqual") return num(actual) >= num(expected);
  if (op === "lessThan") return num(actual) < num(expected);
  if (op === "lessThanOrEqual") return num(actual) <= num(expected);

  // Date (lexicographic ISO compare is order-correct for YYYY-MM-DD)
  if (op === "before") return String(actual) < String(expected);
  if (op === "after") return String(actual) > String(expected);

  // Single-select membership.
  // An object-shaped answer is not a valid equality-family target -> no match (FU-1).
  if (op === "isOneOf") {
    if (isObjectShape(actual)) return false;
    return asList(expected).some((v) => deepEqual(v, actual));
  }

  // Multi-select set operators
  if (op === "includesAll" || op === "includesAny" || op === "includesOnly" || op === "doesNotInclude") {
    const selected = new Set(asList(actual));
    const target = asList(expected);
    if (op === "includesAll") return target.every((t) => selected.has(t));
    if (op === "includesAny") return target.some((t) => selected.has(t));
    if (op === "includesOnly") {
      const targetSet = new Set(target);
      return selected.size === targetSet.size && [...selected].every((s) => targetSet.has(s));
    }
    // doesNotInclude
    return target.every((t) => !selected.has(t));
  }

  throw new Error(`Unknown operator: ${op}`);
}

function evalCondition(node: Json, answers: Record<string, Json>): boolean {
  if (node && typeof node === "object" && "connector" in node) {
    const results = node.conditions.map((c: Json) => evalCondition(c, answers));
    return node.connector === "and"
      ? results.every(Boolean)
      : results.some(Boolean); // "or"
  }
  return evalLeaf(node, answers);
}

// --------------------------------------------------------------------------- //
//  Survey variables + the `calculate` action (Formbricks parity)              //
//                                                                             //
//  The LINE-FOR-LINE TS twin of apps/surveys/logic.py's variable replay.      //
//  ADDITIVE + backward-compatible. When a survey has NO variables AND NO       //
//  calculate actions, the fast-path guard (`surveyHasVariables`) is false and  //
//  every public entry point runs the EXACT pre-existing path on the ORIGINAL   //
//  answers object — byte-identical to today (the frozen corpus proves it).     //
//                                                                             //
//  REPLAY SEMANTICS (frozen; identical in Python compute_variables):           //
//    computeVariables(survey, answers):                                        //
//      vars = seed from survey.variables[].initialValue (0 / "")               //
//      for question in survey.questions (ARRAY ORDER):                         //
//        if !(question.id in answers): continue                                //
//        for rule in question.logic (ARRAY ORDER):                             //
//          enriched = {...answers, ...vars}    // vars visible to conditions   //
//          if !evalCondition(rule.condition, enriched): continue               //
//          if action.type === "calculate": apply(vars, action, enriched);      //
//                                           continue   // NON-navigation       //
//          if action.type in (jumpTo|skip|endSurvey): break  // nav HALTS scan //
//          // require: keep scanning                                           //
//      return vars                                                             //
//                                                                             //
//  DETERMINISM GUARDS (explicit, must match Python):                           //
//    * divide-by-zero -> no-op   * non-numeric numeric operand -> no-op        //
//    * unresolvable/empty ref -> no-op                                         //
//    * text op: recall's render rule (scalars stringify; bool -> "Yes"/"No";   //
//      object-shaped -> no-op)                                                 //
// --------------------------------------------------------------------------- //
const NUMERIC_CALC_OPS = new Set(["assign", "add", "subtract", "multiply", "divide"]);
const TEXT_CALC_OPS = new Set(["assign", "concat"]);

function variableDefs(survey: Json): Json[] {
  const vs = survey.variables;
  return Array.isArray(vs) ? vs : [];
}

function questionHasCalculate(survey: Json): boolean {
  for (const q of survey.questions ?? []) {
    for (const rule of q.logic ?? []) {
      if (rule.action && rule.action.type === "calculate") return true;
    }
  }
  return false;
}

/** Fast-path guard: true iff the survey defines any variable OR any calculate action.
 *  When false, the evaluator MUST run the exact pre-variable path on the original answers
 *  object (byte-identical to today — the frozen corpus guarantee). */
function surveyHasVariables(survey: Json): boolean {
  if (variableDefs(survey).length > 0) return true;
  return questionHasCalculate(survey);
}

/** Render an operand to display text for a text op (mirrors recall renderValue + the Python
 *  _render_scalar_for_text). Returns null for object-shaped / unrenderable operands. */
function renderScalarForText(value: Json): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) {
    const parts = value
      .filter((v) => (typeof v === "string" || typeof v === "number") && typeof v !== "boolean")
      .map((v) => String(v));
    return parts.length > 0 ? parts.join(", ") : null;
  }
  return null; // object / other -> not renderable
}

/** Resolve a #/$defs/calcValue operand: literal -> its value; ref -> the enriched-map value. */
function resolveCalcOperand(valueSpec: Json, enriched: Record<string, Json>): Json {
  if (!valueSpec || typeof valueSpec !== "object") return null;
  if (valueSpec.type === "literal") return valueSpec.value;
  if (valueSpec.type === "ref") return enriched[valueSpec.ref];
  return null;
}

/** Coerce to a finite number for a numeric calc, or null (bool/string/etc. -> null). */
function tryNum(value: Json): number | null {
  if (typeof value === "boolean") return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return null;
}

/** Apply ONE calculate action to `variables` in place. Type-incompatible op / unresolvable
 *  operand / non-numeric numeric operand / divide-by-zero -> deterministic NO-OP (variable
 *  unchanged). Identical to the Python twin. */
function applyCalculate(
  variables: Record<string, Json>,
  varTypes: Record<string, string>,
  action: Json,
  enriched: Record<string, Json>,
): void {
  const varId: string = action.variableId;
  const op: string = action.operation;
  if (varId === undefined || !(varId in varTypes)) return; // unknown variable -> no-op
  const vtype = varTypes[varId];
  const operand = resolveCalcOperand(action.value, enriched);

  if (vtype === "number") {
    if (!NUMERIC_CALC_OPS.has(op)) return;
    if (op === "assign") {
      const n = tryNum(operand);
      if (n === null) return;
      variables[varId] = n;
      return;
    }
    const operandN = tryNum(operand);
    if (operandN === null) return; // non-numeric operand -> no-op
    let current = tryNum(variables[varId]);
    if (current === null) current = 0;
    if (op === "add") variables[varId] = current + operandN;
    else if (op === "subtract") variables[varId] = current - operandN;
    else if (op === "multiply") variables[varId] = current * operandN;
    else if (op === "divide") {
      if (operandN === 0) return; // divide-by-zero -> no-op
      variables[varId] = current / operandN;
    }
    return;
  }

  if (vtype === "text") {
    if (!TEXT_CALC_OPS.has(op)) return;
    const rendered = renderScalarForText(operand);
    if (rendered === null) return; // unrenderable operand -> no-op
    if (op === "assign") variables[varId] = rendered;
    else if (op === "concat") {
      const current = variables[varId];
      const currentStr = typeof current === "string" ? current : "";
      variables[varId] = currentStr + rendered;
    }
    return;
  }
}

/**
 * Deterministically replay the survey's `calculate` actions over the answered path and
 * return the live `{variableId: value}` map. PURE + stateless (recomputed from `answers`
 * alone, like the skip set). Exported so the runtime preview path + recall callers can read
 * variable values. When the survey has no variables, returns `{}` (byte-identical to today).
 */
export function computeVariables(
  survey: Survey,
  answersSoFar: AnswerMap,
): Record<string, Json> {
  const s = survey as Json;
  const defs = variableDefs(s);
  if (defs.length === 0) return {};
  const answers = (answersSoFar ?? {}) as Record<string, Json>;

  const variables: Record<string, Json> = {};
  const varTypes: Record<string, string> = {};
  for (const v of defs) {
    if (!v || typeof v !== "object" || !("id" in v)) continue;
    const vid: string = v.id;
    const vtype: string = v.type ?? "text";
    varTypes[vid] = vtype;
    const hasInit = Object.prototype.hasOwnProperty.call(v, "initialValue");
    const init: Json = hasInit ? v.initialValue : vtype === "number" ? 0 : "";
    if (vtype === "number") {
      const n = tryNum(init);
      variables[vid] = n === null ? 0 : n;
    } else {
      variables[vid] = typeof init === "string" ? init : "";
    }
  }

  for (const q of s.questions ?? []) {
    if (!has(answers, q.id)) continue;
    for (const rule of q.logic ?? []) {
      const enriched = { ...answers, ...variables };
      if (!evalCondition(rule.condition, enriched)) continue;
      const action = rule.action ?? {};
      const atype = action.type;
      if (atype === "calculate") {
        applyCalculate(variables, varTypes, action, enriched);
        continue; // non-navigation; keep scanning
      }
      if (atype === "jumpTo" || atype === "skip" || atype === "endSurvey") {
        break; // a matching navigation rule halts the replay for this host
      }
      // 'require' (or any other non-navigation action): keep scanning.
    }
  }
  return variables;
}

/** The answers map a variables-aware survey threads through the evaluator: the raw answers
 *  OVERLAID with the live variable values (so conditions can compare a variable). Returns the
 *  SAME object when the survey has no variables (fast-path keeps the no-variable path pristine). */
function enrichedAnswers(survey: Json, answers: Record<string, Json>): Record<string, Json> {
  const variables = computeVariables(survey as Survey, answers as AnswerMap);
  if (Object.keys(variables).length === 0) return answers;
  return { ...answers, ...variables };
}

// --------------------------------------------------------------------------- //
//  Survey indexing                                                            //
// --------------------------------------------------------------------------- //
function questionIndex(survey: Json): Record<string, number> {
  const idx: Record<string, number> = {};
  (survey.questions ?? []).forEach((q: Json, i: number) => {
    idx[q.id] = i;
  });
  return idx;
}

function endingIds(survey: Json): string[] {
  return (survey.endings ?? []).map((e: Json) => e.id);
}

function firstMatchingNavAction(q: Json, answers: Record<string, Json>): Json | null {
  for (const rule of q.logic ?? []) {
    if (!evalCondition(rule.condition, answers)) continue;
    const action = rule.action;
    if (action.type === "jumpTo" || action.type === "skip" || action.type === "endSurvey") {
      return action;
    }
    // 'require' (or any non-navigation action): not navigation; keep scanning.
  }
  return null;
}

function skippedQuestionIds(survey: Json, answers: Record<string, Json>): Set<string> {
  const skipped = new Set<string>();
  for (const q of survey.questions ?? []) {
    if (!has(answers, q.id)) continue;
    const action = firstMatchingNavAction(q, answers);
    if (action && action.type === "skip") {
      const targets = Array.isArray(action.target) ? action.target : [action.target];
      for (const t of targets) skipped.add(t);
    }
  }
  return skipped;
}

function resolveEnd(survey: Json): NextStep {
  const endings = endingIds(survey);
  return endings.length > 0 ? ending(endings[0]) : end();
}

function nextLinearAfter(survey: Json, startIndex: number, skipped: Set<string>): NextStep {
  const questions = survey.questions ?? [];
  for (let i = startIndex + 1; i < questions.length; i++) {
    const qid = questions[i].id;
    if (skipped.has(qid)) continue;
    return question(qid);
  }
  return resolveEnd(survey);
}

// --------------------------------------------------------------------------- //
//  PUBLIC CONTRACT                                                            //
// --------------------------------------------------------------------------- //
/**
 * Decide the next step (question / ending / end-of-survey) given the survey, the
 * answers so far (including the just-submitted answer for `currentQuestionId`),
 * and the question that was just answered (`null` = start of survey).
 *
 * Evaluation order is FROZEN — see README §2 / survey-schema.json action $comment.
 */
export function nextStep(
  survey: Survey,
  answersSoFar: AnswerMap,
  currentQuestionId: string | null,
): NextStep {
  const s = survey as Json;
  let answers = (answersSoFar ?? {}) as Record<string, Json>;
  // VARIABLES (additive): enrich the answers with live variable values so a condition can
  // compare a variable. Fast-path guard keeps a no-variable survey on the EXACT pre-variable
  // path with the original answers object — byte-identical to today (frozen corpus proves it).
  if (surveyHasVariables(s)) answers = enrichedAnswers(s, answers);
  const qIndex = questionIndex(s);
  const endIds = new Set(endingIds(s));
  const skipped = skippedQuestionIds(s, answers);

  // Start of survey: first non-skipped question (or an ending if none).
  if (currentQuestionId === null || currentQuestionId === undefined) {
    return nextLinearAfter(s, -1, skipped);
  }

  if (!(currentQuestionId in qIndex)) {
    throw new Error(`currentQuestionId ${currentQuestionId} is not a question in this survey`);
  }

  const questions = s.questions;
  const current = questions[qIndex[currentQuestionId]];
  const action = firstMatchingNavAction(current, answers);

  if (action) {
    if (action.type === "endSurvey") {
      const endingId = action.endingId;
      if (endingId !== undefined && endingId !== null) return ending(endingId);
      return resolveEnd(s);
    }
    if (action.type === "jumpTo") {
      const target = action.target;
      if (endIds.has(target)) return ending(target);
      if (target in qIndex) return question(target); // explicit target is authoritative (README §3)
      throw new Error(`jumpTo target ${target} is neither a question nor an ending`);
    }
    if (action.type === "skip") {
      // skip already removed its target(s) from `skipped`; advance linearly.
      return nextLinearAfter(s, qIndex[currentQuestionId], skipped);
    }
  }

  // No navigation rule matched: advance to the next non-skipped question.
  return nextLinearAfter(s, qIndex[currentQuestionId], skipped);
}

/**
 * The set of question ids required for this respondent: every statically required
 * question, PLUS every `require` action target whose rule condition is true given
 * `answersSoFar` — but ONLY for rules whose HOST question has already been answered
 * (require-during-answering; README §3, FROZEN).
 */
export function requiredQuestionIds(survey: Survey, answersSoFar: AnswerMap): Set<string> {
  const s = survey as Json;
  let answers = (answersSoFar ?? {}) as Record<string, Json>;
  // VARIABLES (additive): enrich with live variable values when the survey uses them, so a
  // require rule whose condition compares a variable resolves correctly. No-variable surveys
  // keep the original answers object (byte-identical to today).
  if (surveyHasVariables(s)) answers = enrichedAnswers(s, answers);
  const required = new Set<string>();
  for (const q of s.questions ?? []) {
    if (q.required === true) required.add(q.id);
    // require rules apply only once their HOST question is answered.
    if (!has(answers, q.id)) continue;
    for (const rule of q.logic ?? []) {
      if (rule.action.type === "require" && evalCondition(rule.condition, answers)) {
        required.add(rule.action.target);
      }
    }
  }
  return required;
}

// --------------------------------------------------------------------------- //
//  validateSurvey — in-code checks JSON Schema cannot express (README §4)     //
// --------------------------------------------------------------------------- //
const TEXT_OPS = new Set([
  "equals", "notEquals", "contains", "doesNotContain", "startsWith", "endsWith",
  "isSubmitted", "isSkipped", "isEmpty", "isNotEmpty",
]);
const SINGLE_OPS = new Set(["equals", "notEquals", "isOneOf", "isSubmitted", "isSkipped"]);
const MULTI_OPS = new Set(["includesAll", "includesAny", "includesOnly", "doesNotInclude", "isSubmitted", "isSkipped"]);
const NUMERIC_OPS = new Set([
  "equals", "notEquals", "greaterThan", "greaterThanOrEqual", "lessThan",
  "lessThanOrEqual", "isOneOf", "isSubmitted", "isSkipped",
]);
const DATE_OPS = new Set(["equals", "notEquals", "before", "after", "isSubmitted", "isSkipped"]);
const CONSENT_OPS = new Set(["isAccepted", "isNotAccepted", "isSubmitted", "isSkipped"]);
const CTA_OPS = new Set(["isClicked", "isSkipped"]);
const MATRIX_RANKING_OPS = new Set(["isSubmitted", "isSkipped"]);

const OPS_BY_TYPE: Record<string, Set<string>> = {
  openText: TEXT_OPS,
  contactInfo: TEXT_OPS,
  address: TEXT_OPS, // object-shaped like contactInfo; FU-1 no-match still applies
  singleSelect: SINGLE_OPS,
  pictureChoice: new Set([...SINGLE_OPS, ...MULTI_OPS]),
  multiSelect: MULTI_OPS,
  rating: NUMERIC_OPS,
  nps: NUMERIC_OPS,
  number: NUMERIC_OPS,
  date: DATE_OPS,
  consent: CONSENT_OPS,
  cta: CTA_OPS,
  matrix: MATRIX_RANKING_OPS,
  ranking: MATRIX_RANKING_OPS,
  scheduleMeeting: MATRIX_RANKING_OPS, // booking object is opaque -> submitted/skipped only
  hidden: TEXT_OPS,
};

function* iterLeaves(node: Json): Generator<Json> {
  if (node && typeof node === "object" && "connector" in node) {
    for (const c of node.conditions) yield* iterLeaves(c);
  } else {
    yield node;
  }
}

export interface ValidationError {
  code: string;
  message: string;
  [k: string]: Json;
}

export type ValidationResult = { ok: true } | { ok: false; errors: ValidationError[] };

/**
 * The three in-code survey checks JSON Schema cannot express (README §4):
 *   1. unique question + ending ids
 *   2. no backward `jumpTo`/`require`/`skip` target (and targets must exist)
 *   3. operator/type validity + at-or-before condition refs
 *
 * The evaluator assumes a validated survey; the runtime + builder run this.
 */
export function validateSurvey(survey: Survey): ValidationResult {
  const s = survey as Json;
  const errors: ValidationError[] = [];
  const questions = s.questions ?? [];
  const qIndex: Record<string, number> = {};
  const qType: Record<string, string> = {};
  questions.forEach((q: Json, i: number) => {
    qIndex[q.id] = i;
    qType[q.id] = q.type;
  });
  const endIds = new Set(endingIds(s));

  // Variable defs: {id: "number"|"text"}. A variable id is a valid condition ref + recall
  // source + calculate target; it joins the duplicate_id namespace below.
  const variableTypes: Record<string, string> = {};
  for (const v of variableDefs(s)) {
    if (v && typeof v === "object" && "id" in v) variableTypes[v.id] = v.type ?? "text";
  }
  // The full set of ids that resolve as a value source (questions + hidden fields + variables)
  // — used by calculate `value` ref validation.
  const valueSourceIds = new Set<string>(Object.keys(qIndex));
  for (const hf of s.hiddenFields ?? []) {
    if (hf && typeof hf.id === "string") valueSourceIds.add(hf.id);
  }
  for (const vid of Object.keys(variableTypes)) valueSourceIds.add(vid);

  // (1) unique ids across questions + endings
  const seen = new Set<string>();
  for (const q of questions) {
    if (seen.has(q.id)) errors.push({ code: "duplicate_id", message: `Duplicate id ${q.id}`, id: q.id });
    seen.add(q.id);
  }
  for (const e of s.endings ?? []) {
    if (seen.has(e.id)) errors.push({ code: "duplicate_id", message: `Duplicate id ${e.id}`, id: e.id });
    seen.add(e.id);
  }
  // Hidden-field ids share the recall/Response.data namespace with question ids; a
  // collision with a question/ending id (or another hidden field) is a duplicate_id.
  for (const hf of s.hiddenFields ?? []) {
    if (!hf || typeof hf.id !== "string") continue;
    if (seen.has(hf.id)) errors.push({ code: "duplicate_id", message: `Duplicate id ${hf.id}`, id: hf.id });
    seen.add(hf.id);
  }
  // Variable ids share the recall/condition id namespace too, so a collision with a
  // question/ending/hidden-field id (or another variable) is a duplicate_id.
  for (const v of variableDefs(s)) {
    if (!v || typeof v.id !== "string") continue;
    if (seen.has(v.id)) errors.push({ code: "duplicate_id", message: `Duplicate id ${v.id}`, id: v.id });
    seen.add(v.id);
  }

  // (2) + (3) per-question logic checks
  questions.forEach((q: Json, hostIdx: number) => {
    for (const rule of q.logic ?? []) {
      const action = rule.action;

      if (action.type === "jumpTo" || action.type === "require") {
        const target = action.target;
        if (endIds.has(target)) {
          // endings are ahead
        } else if (!(target in qIndex)) {
          errors.push({ code: "unknown_target", message: `${action.type} target ${target} does not exist`, ruleId: rule.id });
        } else if (qIndex[target] <= hostIdx) {
          errors.push({ code: "backward_jump", message: `${action.type} target ${target} is not after the host question`, ruleId: rule.id });
        }
      } else if (action.type === "skip") {
        const targets = Array.isArray(action.target) ? action.target : [action.target];
        for (const t of targets) {
          if (!(t in qIndex)) {
            errors.push({ code: "unknown_target", message: `skip target ${t} does not exist`, ruleId: rule.id });
          } else if (qIndex[t] <= hostIdx) {
            errors.push({ code: "backward_jump", message: `skip target ${t} is not after the host question`, ruleId: rule.id });
          }
        }
      } else if (action.type === "endSurvey") {
        const endingId = action.endingId;
        if (endingId !== undefined && endingId !== null && !endIds.has(endingId)) {
          errors.push({ code: "unknown_target", message: `endSurvey endingId ${endingId} does not exist`, ruleId: rule.id });
        }
      } else if (action.type === "calculate") {
        // calculate is non-navigation: no backward/forward position constraint.
        const varId = action.variableId;
        if (!(varId in variableTypes)) {
          errors.push({ code: "unknown_variable", message: `calculate variableId ${varId} is not a declared variable`, ruleId: rule.id });
        } else {
          const vtype = variableTypes[varId];
          const validCalcOps = vtype === "number" ? NUMERIC_CALC_OPS : TEXT_CALC_OPS;
          if (!validCalcOps.has(action.operation)) {
            errors.push({ code: "invalid_calc_op", message: `operation ${action.operation} invalid for ${vtype} variable ${varId}`, ruleId: rule.id });
          }
        }
        // A `value` REF must resolve to a known value source. A literal carries its own value.
        const valueSpec = action.value;
        if (valueSpec && typeof valueSpec === "object" && valueSpec.type === "ref") {
          if (!valueSourceIds.has(valueSpec.ref)) {
            errors.push({ code: "unknown_calc_ref", message: `calculate value ref ${valueSpec.ref} does not exist`, ruleId: rule.id });
          }
        }
      }

      for (const leaf of iterLeaves(rule.condition)) {
        const ref = leaf.leftQuestionId;
        // A condition leaf may reference a VARIABLE id (additive): a variable has no linear
        // position (always available), so it is exempt from the forward_condition_ref check;
        // its operator validity is driven by its type (number -> numeric, text -> text ops).
        if (ref in variableTypes) {
          const vtype = variableTypes[ref];
          const validOpsV = vtype === "number" ? NUMERIC_OPS : TEXT_OPS;
          if (!validOpsV.has(leaf.operator)) {
            errors.push({ code: "invalid_operator", message: `operator ${leaf.operator} invalid for ${vtype} variable ${ref}`, ruleId: rule.id });
          }
          continue;
        }
        if (!(ref in qIndex)) {
          errors.push({ code: "unknown_condition_ref", message: `condition references unknown question ${ref}`, ruleId: rule.id });
          continue;
        }
        if (qIndex[ref] > hostIdx) {
          errors.push({ code: "forward_condition_ref", message: `condition references a later question ${ref}`, ruleId: rule.id });
        }
        const validOps = OPS_BY_TYPE[qType[ref]] ?? new Set<string>();
        if (!validOps.has(leaf.operator)) {
          errors.push({ code: "invalid_operator", message: `operator ${leaf.operator} invalid for type ${qType[ref]}`, ruleId: rule.id });
        }
      }
    }
  });

  return errors.length > 0 ? { ok: false, errors } : { ok: true };
}
