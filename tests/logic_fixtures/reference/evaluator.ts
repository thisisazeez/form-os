/**
 * Reference TypeScript implementation of the OpenSurvey logic evaluator.
 *
 * ============================================================================
 *   REFERENCE / STAND-IN IMPLEMENTATION — NOT the production evaluator.
 * ============================================================================
 * This module exists so the shared TS<->PY fixture harness is runnable BEFORE
 * Wave 3 ships the real evaluators (PLAN.md §6/§15, contracts/decisions.md
 * ADR-004). It encodes the *frozen* evaluation semantics documented in
 * tests/logic_fixtures/README.md §2-§4 and in contracts/survey-schema.json
 * `#/$defs/action` `$comment`.
 *
 * Wave 3 replaces this with the real runtime/builder evaluator (e.g.
 * `@opensurvey/runtime/logic`) exporting the SAME three functions with the SAME
 * behavior; the runner then imports that module instead (see README §6,
 * "Injecting the real evaluator"). The fixtures do not change.
 *
 * The matching `evaluator.py` in this directory is the line-for-line PY twin.
 * Keep them behaviorally identical — that is the whole point of the corpus.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;

export type NextStep =
  | { type: "question"; id: string }
  | { type: "ending"; id: string }
  | { type: "end" };

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
 * isOneOf->false) — identically in TS and Python. `deepEqual` below already
 * returns false for non-array objects, but we guard EXPLICITLY so the rule is
 * stated in the file (not emergent) and the Python twin matches line-for-line.
 */
function isObjectShape(value: Json): boolean {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function num(value: Json): number {
  if (typeof value === "boolean") throw new Error("boolean is not a numeric operand");
  if (typeof value === "number") return value;
  throw new Error(`non-numeric operand: ${JSON.stringify(value)}`);
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

  // Scalar equality
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

  // Single-select membership
  // An object-shaped answer is not a valid equality-family target -> no match,
  // per the operator `$comment` (FU-1).
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

function deepEqual(a: Json, b: Json): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => deepEqual(x, b[i]));
  }
  return false;
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

function firstMatchingNavAction(question: Json, answers: Record<string, Json>): Json | null {
  for (const rule of question.logic ?? []) {
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
export function nextStep(
  survey: Json,
  answersSoFar: Record<string, Json>,
  currentQuestionId: string | null,
): NextStep {
  const answers = answersSoFar ?? {};
  const qIndex = questionIndex(survey);
  const endIds = new Set(endingIds(survey));
  const skipped = skippedQuestionIds(survey, answers);

  // Start of survey: first non-skipped question (or an ending if none).
  if (currentQuestionId === null || currentQuestionId === undefined) {
    return nextLinearAfter(survey, -1, skipped);
  }

  if (!(currentQuestionId in qIndex)) {
    throw new Error(`currentQuestionId ${currentQuestionId} is not a question in this survey`);
  }

  const questions = survey.questions;
  const current = questions[qIndex[currentQuestionId]];
  const action = firstMatchingNavAction(current, answers);

  if (action) {
    if (action.type === "endSurvey") {
      const endingId = action.endingId;
      if (endingId !== undefined && endingId !== null) return ending(endingId);
      return resolveEnd(survey);
    }
    if (action.type === "jumpTo") {
      const target = action.target;
      if (endIds.has(target)) return ending(target);
      if (target in qIndex) return question(target); // explicit target is authoritative (README §3)
      throw new Error(`jumpTo target ${target} is neither a question nor an ending`);
    }
    if (action.type === "skip") {
      // skip already removed its target(s) from `skipped`; advance linearly.
      return nextLinearAfter(survey, qIndex[currentQuestionId], skipped);
    }
  }

  // No navigation rule matched: advance to the next non-skipped question.
  return nextLinearAfter(survey, qIndex[currentQuestionId], skipped);
}

export function requiredQuestionIds(survey: Json, answersSoFar: Record<string, Json>): Set<string> {
  // FROZEN interpretation (README §3): 'require' actions are "applied during
  // answering" — a require rule on question Q only takes effect once Q itself is
  // in `answersSoFar`. A require rule on a not-yet-answered host does NOT
  // pre-emptively mark its target required. (Fixture: 'required — require on a
  // not-yet-answered host does not apply'.)
  const answers = answersSoFar ?? {};
  const required = new Set<string>();
  for (const q of survey.questions ?? []) {
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
//  validate_survey — in-code checks JSON Schema cannot express (README §4)     //
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

export function validateSurvey(survey: Json): { ok: true } | { ok: false; errors: ValidationError[] } {
  const errors: ValidationError[] = [];
  const questions = survey.questions ?? [];
  const qIndex: Record<string, number> = {};
  const qType: Record<string, string> = {};
  questions.forEach((q: Json, i: number) => {
    qIndex[q.id] = i;
    qType[q.id] = q.type;
  });
  const endIds = new Set(endingIds(survey));

  // (1) unique ids across questions + endings
  const seen = new Set<string>();
  for (const q of questions) {
    if (seen.has(q.id)) errors.push({ code: "duplicate_id", message: `Duplicate id ${q.id}`, id: q.id });
    seen.add(q.id);
  }
  for (const e of survey.endings ?? []) {
    if (seen.has(e.id)) errors.push({ code: "duplicate_id", message: `Duplicate id ${e.id}`, id: e.id });
    seen.add(e.id);
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
      }

      for (const leaf of iterLeaves(rule.condition)) {
        const ref = leaf.leftQuestionId;
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
