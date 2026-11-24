// ============================================================================
// onForm — Builder LOGIC model helpers (pure, no React).
//
// Immutable transforms over the SHARED Survey definition's per-question logic
// (../../../lib/types — the FROZEN contracts/survey-schema.json shape). Logic
// rules are NOT survey-level in the schema: each rule lives in a specific
// question's `logic[]` array (#/$defs/question.logic → #/$defs/logicRule). The
// rule's HOST question is load-bearing for validation (see validateSurvey in
// ../../../lib/logic/evaluator.ts):
//
//   • A rule's `action` target (jumpTo / require / skip) MUST be a question AFTER
//     the host (by array index) or an ending id — backward targets are rejected
//     (`backward_jump`).
//   • Every condition leaf's `leftQuestionId` MUST reference a question AT OR
//     BEFORE the host — forward refs are rejected (`forward_condition_ref`).
//   • The operator MUST be valid for the referenced question's type
//     (`invalid_operator`), per the OPERATOR-VALIDITY table.
//
// To guarantee schema-valid output WITHOUT modifying the shared lib, this module:
//   1. Derives the host from the rule's conditions — the host is the question with
//      the MAX index among all referenced `leftQuestionId`s. This makes every
//      condition an at-or-before ref by construction (no forward_condition_ref).
//   2. Re-hosts a rule whenever its conditions change (a forward condition migrates
//      the host forward). `validateRuleAgainstSurvey` then flags an action target
//      that is no longer after the new host so the UI can force a re-pick.
//   3. Mirrors the evaluator's OPS_BY_TYPE table (OPERATOR_VALIDITY below) so the
//      operator picker only offers operators the validator accepts. The mirror is
//      pinned to the evaluator by a unit test that runs emitted rules through the
//      real validateSurvey.
//
// The UI presents rules as a flat, survey-wide list (matching the mockup), but the
// canonical storage is always per-question `logic[]`. `flattenRules` builds the UI
// view; every mutation writes back through `setRule` / `removeRule`.
// ============================================================================
import type {
  Action,
  ConditionGroup,
  ConditionLeaf,
  LogicRule,
  Operator,
  Question,
  QuestionType,
  Survey,
} from "../../../lib/types";

// --------------------------------------------------------------------------- //
//  Operator validity + friendly labels (mirrors ../../../lib/logic evaluator) //
// --------------------------------------------------------------------------- //
// MUST stay in sync with OPS_BY_TYPE in ../../../lib/logic/evaluator.ts (the
// validator the server + Save run). Pinned by logicModel.test.ts, which feeds
// every operator this table allows through the REAL validateSurvey and asserts
// no `invalid_operator` error. Treating the lib read-only, we cannot import the
// private set; this is the deliberate, test-guarded mirror.
const TEXT_OPS: Operator[] = [
  "equals", "notEquals", "contains", "doesNotContain", "startsWith", "endsWith",
  "isSubmitted", "isSkipped", "isEmpty", "isNotEmpty",
];
const SINGLE_OPS: Operator[] = ["equals", "notEquals", "isOneOf", "isSubmitted", "isSkipped"];
const MULTI_OPS: Operator[] = [
  "includesAll", "includesAny", "includesOnly", "doesNotInclude", "isSubmitted", "isSkipped",
];
const NUMERIC_OPS: Operator[] = [
  "equals", "notEquals", "greaterThan", "greaterThanOrEqual", "lessThan",
  "lessThanOrEqual", "isOneOf", "isSubmitted", "isSkipped",
];
const DATE_OPS: Operator[] = ["equals", "notEquals", "before", "after", "isSubmitted", "isSkipped"];
const CONSENT_OPS: Operator[] = ["isAccepted", "isNotAccepted", "isSubmitted", "isSkipped"];
const CTA_OPS: Operator[] = ["isClicked", "isSkipped"];
const MATRIX_RANKING_OPS: Operator[] = ["isSubmitted", "isSkipped"];

/** Operators the validator accepts for a given question type (ordered for the picker). */
export const OPERATOR_VALIDITY: Record<QuestionType, Operator[]> = {
  openText: TEXT_OPS,
  contactInfo: TEXT_OPS,
  address: TEXT_OPS, // object-shaped like contactInfo; FU-1 no-match still applies
  hidden: TEXT_OPS,
  singleSelect: SINGLE_OPS,
  pictureChoice: dedupe([...SINGLE_OPS, ...MULTI_OPS]),
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
  // fileUpload has NO entry in the evaluator's OPS_BY_TYPE table (so its valid-op set
  // is empty and validateSurvey rejects ANY operator on it). Mirror that: no operator
  // is offered, so the author can't author an invalid fileUpload condition.
  fileUpload: [],
};

function dedupe<T>(xs: T[]): T[] {
  return [...new Set(xs)];
}

/** Human-readable operator labels for the IF dropdown (schema value -> label). */
export const OPERATOR_LABEL: Record<Operator, string> = {
  equals: "is equal to",
  notEquals: "is not equal to",
  contains: "contains",
  doesNotContain: "does not contain",
  startsWith: "starts with",
  endsWith: "ends with",
  greaterThan: "is greater than",
  greaterThanOrEqual: "is greater than or equal to",
  lessThan: "is less than",
  lessThanOrEqual: "is less than or equal to",
  before: "is before",
  after: "is after",
  isOneOf: "is one of",
  includesAll: "includes all of",
  includesAny: "includes any of",
  includesOnly: "includes only",
  doesNotInclude: "does not include",
  isAccepted: "is accepted",
  isNotAccepted: "is not accepted",
  isClicked: "was clicked",
  isSubmitted: "is answered",
  isSkipped: "was skipped",
  isEmpty: "is empty",
  isNotEmpty: "is filled",
};

/** Operators that take NO right-hand value (presence / boolean operators). */
const VALUELESS_OPS = new Set<Operator>([
  "isSubmitted", "isSkipped", "isEmpty", "isNotEmpty",
  "isAccepted", "isNotAccepted", "isClicked",
]);

export function operatorTakesValue(op: Operator): boolean {
  return !VALUELESS_OPS.has(op);
}

/** Operators whose value is a single CHOICE id (rendered as a choice picker). */
const SINGLE_CHOICE_VALUE_OPS = new Set<Operator>(["equals", "notEquals", "isOneOf"]);
/** Operators whose value is a set of CHOICE ids. */
const MULTI_CHOICE_VALUE_OPS = new Set<Operator>([
  "includesAll", "includesAny", "includesOnly", "doesNotInclude",
]);

/** How the value editor should render for a (questionType, operator) pair. */
export type ValueKind = "none" | "text" | "number" | "date" | "singleChoice" | "multiChoice";

export function valueKind(qType: QuestionType, op: Operator): ValueKind {
  if (!operatorTakesValue(op)) return "none";
  const choiceType = qType === "singleSelect" || qType === "multiSelect" || qType === "pictureChoice";
  if (choiceType && MULTI_CHOICE_VALUE_OPS.has(op)) return "multiChoice";
  if (choiceType && SINGLE_CHOICE_VALUE_OPS.has(op)) return "singleChoice";
  if (qType === "rating" || qType === "nps" || qType === "number") return "number";
  if (qType === "date") return "date";
  return "text";
}

/** True if `op` is in the validity table for `qType`. */
export function isOperatorValidForType(qType: QuestionType, op: Operator): boolean {
  return (OPERATOR_VALIDITY[qType] ?? []).includes(op);
}

/** The first valid operator for a question type (the picker's default). */
export function defaultOperatorForType(qType: QuestionType): Operator {
  const ops = OPERATOR_VALIDITY[qType];
  return ops && ops.length > 0 ? ops[0] : "isSubmitted";
}

// --------------------------------------------------------------------------- //
//  Action catalogue (the 4 schema action types, with friendly labels)         //
// --------------------------------------------------------------------------- //
export type ActionType = Action["type"]; // "jumpTo" | "skip" | "endSurvey" | "require" | "calculate"

export const ACTION_LABEL: Record<ActionType, string> = {
  jumpTo: "Jump to",
  skip: "Skip",
  endSurvey: "End survey",
  require: "Make required",
  calculate: "Calculate",
};

/** Whether an action needs a (question/ending) target picker. `calculate` has its OWN editor
 *  (variable + operation + value), not the shared forward-target picker. */
export function actionTakesTarget(type: ActionType): boolean {
  return type === "jumpTo" || type === "skip" || type === "require";
}

/** Calc operations valid per variable type (mirrors apps.surveys.logic + the evaluator). */
export const NUMBER_CALC_OPS = ["assign", "add", "subtract", "multiply", "divide"] as const;
export const TEXT_CALC_OPS = ["assign", "concat"] as const;

export const CALC_OP_LABEL: Record<string, string> = {
  assign: "set to",
  add: "add",
  subtract: "subtract",
  multiply: "multiply by",
  divide: "divide by",
  concat: "append",
};

/** The calc operations a given variable type allows (for the operation picker). */
export function calcOpsForVariableType(type: "number" | "text"): readonly string[] {
  return type === "number" ? NUMBER_CALC_OPS : TEXT_CALC_OPS;
}

// --------------------------------------------------------------------------- //
//  id generation (schema-valid: #/$defs/logicRule.id ^[a-zA-Z0-9_-]{1,64}$)    //
// --------------------------------------------------------------------------- //
function shortId(len = 6): string {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < len; i++) out += alphabet[Math.floor(Math.random() * alphabet.length)];
  return out;
}

/** All rule ids currently present anywhere in the survey (across every host). */
export function allRuleIds(survey: Survey): Set<string> {
  const ids = new Set<string>();
  for (const q of survey.questions) {
    for (const rule of q.logic ?? []) ids.add(rule.id);
  }
  return ids;
}

/** A new, survey-unique rule id matching the schema pattern. */
export function newRuleId(survey: Survey): string {
  const taken = allRuleIds(survey);
  let id = `rule_${shortId()}`;
  while (taken.has(id)) id = `rule_${shortId()}`;
  return id;
}

// --------------------------------------------------------------------------- //
//  Condition tree helpers                                                      //
// --------------------------------------------------------------------------- //
export function isLeaf(node: ConditionGroup): node is ConditionLeaf {
  return !(typeof node === "object" && node !== null && "connector" in node);
}

/** All condition leaves of a rule, in tree order (left-to-right). */
export function ruleLeaves(rule: LogicRule): ConditionLeaf[] {
  const out: ConditionLeaf[] = [];
  const walk = (node: ConditionGroup) => {
    if (isLeaf(node)) out.push(node);
    else node.conditions.forEach(walk);
  };
  walk(rule.condition);
  return out;
}

/**
 * Normalize a rule's condition into a FLAT shape for the UI:
 *   - a single leaf, OR
 *   - a single connector ("and"/"or") whose children are ALL leaves.
 *
 * The mockup's IF block is a flat list of leaves joined by one connector; we keep
 * the stored shape isomorphic to that. (The schema permits arbitrary nesting; the
 * builder authors only the flat form. Deeply-nested rules loaded from elsewhere are
 * flattened for display by collecting their leaves — still schema-valid on write.)
 */
export interface FlatCondition {
  connector: "and" | "or";
  leaves: ConditionLeaf[];
}

export function toFlatCondition(node: ConditionGroup): FlatCondition {
  if (isLeaf(node)) return { connector: "and", leaves: [node] };
  // collector node — gather direct + nested leaves; remember the top connector.
  const leaves: ConditionLeaf[] = [];
  const walk = (n: ConditionGroup) => {
    if (isLeaf(n)) leaves.push(n);
    else n.conditions.forEach(walk);
  };
  walk(node);
  return { connector: node.connector, leaves };
}

/** Rebuild a ConditionGroup from a flat shape (single leaf stays a leaf). */
export function fromFlatCondition(flat: FlatCondition): ConditionGroup {
  if (flat.leaves.length === 1) return flat.leaves[0];
  return { connector: flat.connector, conditions: flat.leaves };
}

// --------------------------------------------------------------------------- //
//  Host derivation (load-bearing for validation)                              //
// --------------------------------------------------------------------------- //
function questionIndex(survey: Survey): Record<string, number> {
  const idx: Record<string, number> = {};
  survey.questions.forEach((q, i) => (idx[q.id] = i));
  return idx;
}

/**
 * The index of the rule's HOST question = the MAX index among the questions its
 * conditions reference. Unknown refs are ignored. Returns 0 when the rule has no
 * resolvable refs (e.g. all refs were deleted) so the rule still attaches somewhere
 * valid; the caller can also drop such a rule.
 */
export function hostIndexForRule(survey: Survey, rule: LogicRule): number {
  const idx = questionIndex(survey);
  let max = -1;
  for (const leaf of ruleLeaves(rule)) {
    const i = idx[leaf.leftQuestionId];
    if (i !== undefined && i > max) max = i;
  }
  return max < 0 ? 0 : max;
}

// --------------------------------------------------------------------------- //
//  Flat (survey-wide) rule view for the UI                                    //
// --------------------------------------------------------------------------- //
export interface FlatRule {
  rule: LogicRule;
  /** The id of the question this rule is stored on (its host). */
  hostQuestionId: string;
  /** The host's index in survey.questions. */
  hostIndex: number;
}

/**
 * Flatten every question's `logic[]` into one survey-wide ordered list, tagging each
 * rule with its host. Order: by host index, then by the rule's position within the
 * host's logic[] (stable). This is the list the LogicView renders.
 */
export function flattenRules(survey: Survey): FlatRule[] {
  const out: FlatRule[] = [];
  survey.questions.forEach((q, hostIndex) => {
    for (const rule of q.logic ?? []) {
      out.push({ rule, hostQuestionId: q.id, hostIndex });
    }
  });
  return out;
}

// --------------------------------------------------------------------------- //
//  Immutable write helpers (every transform returns a fresh Survey)           //
// --------------------------------------------------------------------------- //
/** Remove a rule (by id) from whichever host currently holds it. */
function stripRule(survey: Survey, ruleId: string): Survey {
  return {
    ...survey,
    questions: survey.questions.map((q) => {
      const logic = q.logic ?? [];
      if (!logic.some((r) => r.id === ruleId)) return q;
      return { ...q, logic: logic.filter((r) => r.id !== ruleId) };
    }),
  };
}

/** Append a rule to a question's `logic[]` (creating the array if absent). */
function appendRuleToQuestion(question: Question, rule: LogicRule): Question {
  return { ...question, logic: [...(question.logic ?? []), rule] };
}

/**
 * Insert / replace a rule, RE-HOSTING it onto the question derived from its
 * conditions (max-condition-ref-index). Removes any prior copy first, then appends
 * to the correct host. This is the single funnel every condition/action edit goes
 * through, so the host invariant (conditions are at-or-before; see header) always
 * holds on write.
 */
export function setRule(survey: Survey, rule: LogicRule): Survey {
  const stripped = stripRule(survey, rule.id);
  const hostIdx = hostIndexForRule(stripped, rule);
  const host = stripped.questions[hostIdx];
  if (!host) return stripped; // no questions — nothing to host on
  return {
    ...stripped,
    questions: stripped.questions.map((q, i) =>
      i === hostIdx ? appendRuleToQuestion(q, rule) : q,
    ),
  };
}

/** Delete a rule entirely (from its host's logic[]). */
export function removeRule(survey: Survey, ruleId: string): Survey {
  return stripRule(survey, ruleId);
}

/**
 * Reorder a rule by one slot (`dir` = -1 up, +1 down) WITHIN its host question's
 * `logic[]`. Rule order is only meaningful within a host — the evaluator fires the
 * FIRST matching rule on a host (firstMatchingNavAction), so swapping two rules on
 * the same question changes precedence. A rule cannot change host via reorder (its
 * host is derived from its conditions), so a move that would cross into another
 * host's block is a NO-OP. Returns a fresh Survey (or the same ref on a no-op).
 */
export function reorderRule(survey: Survey, ruleId: string, dir: -1 | 1): Survey {
  let changed = false;
  const questions = survey.questions.map((q) => {
    const logic = q.logic ?? [];
    const i = logic.findIndex((r) => r.id === ruleId);
    if (i < 0) return q;
    const j = i + dir;
    if (j < 0 || j >= logic.length) return q; // at the edge of this host's block
    const next = [...logic];
    [next[i], next[j]] = [next[j], next[i]];
    changed = true;
    return { ...q, logic: next };
  });
  // Return the SAME reference on a true no-op so dirty-tracking doesn't fire.
  return changed ? { ...survey, questions } : survey;
}

// --------------------------------------------------------------------------- //
//  Rule factories (seed schema-valid starting points)                         //
// --------------------------------------------------------------------------- //
/**
 * A brand-new rule. Seeded with ONE condition leaf referencing `questions[0]` with
 * `isSubmitted` (universally valid for every type, needs no value) and a `jumpTo`
 * action targeting the first question/ending AFTER the host. If the survey has no
 * questions, returns null (the caller blocks "Add rule" with an empty-state).
 *
 * Guarantees the freshly-added rule passes validateSurvey:
 *   - condition ref (q0) is at-or-before the host (host == q0 when only q0 is
 *     referenced) → no forward_condition_ref.
 *   - `isSubmitted` is valid for every type → no invalid_operator.
 *   - the action target is the first valid forward target → no backward_jump /
 *     unknown_target; falls back to endSurvey (no endingId) when nothing is after
 *     the host.
 */
export function makeRule(survey: Survey): LogicRule | null {
  if (survey.questions.length === 0) return null;
  const firstQ = survey.questions[0];
  const leaf: ConditionLeaf = { leftQuestionId: firstQ.id, operator: "isSubmitted" };
  const rule: LogicRule = {
    id: newRuleId(survey),
    condition: leaf,
    action: { type: "endSurvey" }, // safe default; refined below to a forward target
  };
  // Host is q0 (only ref). Prefer jumping to the next question if one exists.
  const hostIdx = 0;
  const action = firstForwardAction(survey, hostIdx);
  return { ...rule, action };
}

/** A default action for a rule hosted at `hostIdx`: jump to the next question, else
 *  the first ending, else endSurvey (no endingId → end-of-survey). */
export function firstForwardAction(survey: Survey, hostIdx: number): Action {
  const next = survey.questions[hostIdx + 1];
  if (next) return { type: "jumpTo", target: next.id };
  const endings = survey.endings ?? [];
  if (endings.length > 0) return { type: "jumpTo", target: endings[0].id };
  return { type: "endSurvey" };
}

// --------------------------------------------------------------------------- //
//  Condition leaf edits (return a NEW leaf; caller rebuilds the rule)         //
// --------------------------------------------------------------------------- //
/**
 * Change a leaf's referenced question. Resets the operator to the new type's default
 * and drops the value (the prior value rarely fits the new type). Keeps the leaf
 * schema-valid by construction.
 */
export function setLeafQuestion(
  qid: string,
  qType: QuestionType,
): ConditionLeaf {
  const op = defaultOperatorForType(qType);
  return cleanLeafValue({ leftQuestionId: qid, operator: op }, qType, op);
}

/** Change a leaf's operator; drop/keep the value per the new operator's value-kind. */
export function setLeafOperator(
  leaf: ConditionLeaf,
  op: Operator,
  qType: QuestionType,
): ConditionLeaf {
  return cleanLeafValue({ ...leaf, operator: op }, qType, op);
}

/** Set a leaf's scalar value (text/number/date/singleChoice). */
export function setLeafValue(
  leaf: ConditionLeaf,
  value: string | number,
): ConditionLeaf {
  return { ...leaf, value };
}

/** Set a leaf's array value (multiChoice / isOneOf as a set). */
export function setLeafArrayValue(
  leaf: ConditionLeaf,
  values: (string | number)[],
): ConditionLeaf {
  return { ...leaf, value: values };
}

/**
 * Normalize a leaf's `value` to its operator's value-kind:
 *   - valueless ops → drop `value`
 *   - multiChoice → ensure an array
 *   - number → coerce numeric (drop if NaN)
 *   - otherwise keep a scalar (default "")
 */
function cleanLeafValue(leaf: ConditionLeaf, qType: QuestionType, op: Operator): ConditionLeaf {
  const kind = valueKind(qType, op);
  const next: ConditionLeaf = { leftQuestionId: leaf.leftQuestionId, operator: op };
  if (kind === "none") return next; // no value key at all
  if (kind === "multiChoice") {
    next.value = Array.isArray(leaf.value) ? leaf.value : [];
    return next;
  }
  if (kind === "number") {
    next.value = typeof leaf.value === "number" ? leaf.value : 0;
    return next;
  }
  // text / date / singleChoice: a scalar string (singleChoice = a choiceId string).
  next.value = typeof leaf.value === "string" || typeof leaf.value === "number" ? leaf.value : "";
  return next;
}

// --------------------------------------------------------------------------- //
//  Per-rule validation (mirrors validateSurvey, scoped to ONE rule)           //
// --------------------------------------------------------------------------- //
export interface RuleIssue {
  code:
    | "backward_jump"
    | "unknown_target"
    | "forward_condition_ref"
    | "unknown_condition_ref"
    | "invalid_operator"
    | "no_target"
    | "unknown_variable"
    | "invalid_calc_op"
    | "unknown_calc_ref";
  message: string;
}

/** {variableId: type} for the survey's declared variables (for calc + condition-ref checks). */
export function variableTypeMap(survey: Survey): Record<string, "number" | "text"> {
  const out: Record<string, "number" | "text"> = {};
  for (const v of survey.variables ?? []) {
    if (v && typeof v.id === "string") out[v.id] = v.type === "number" ? "number" : "text";
  }
  return out;
}

/**
 * Validate ONE rule against the survey, mirroring the same checks validateSurvey
 * applies (../../../lib/logic/evaluator.ts). Used by the rule card to show inline
 * notices (esp. after a re-host invalidates the action target). This is a CONVENIENCE
 * mirror for UX; Save still runs the authoritative shared validateSurvey.
 */
export function validateRuleAgainstSurvey(survey: Survey, rule: LogicRule): RuleIssue[] {
  const issues: RuleIssue[] = [];
  const idx = questionIndex(survey);
  const endIds = new Set((survey.endings ?? []).map((e) => e.id));
  const qType: Record<string, QuestionType> = {};
  survey.questions.forEach((q) => (qType[q.id] = q.type));
  const varTypes = variableTypeMap(survey);
  // value sources for a calculate `ref` (question / variable / hidden field ids).
  const valueSourceIds = new Set<string>([
    ...survey.questions.map((q) => q.id),
    ...(survey.hiddenFields ?? []).map((f) => f.id),
    ...Object.keys(varTypes),
  ]);
  const hostIdx = hostIndexForRule(survey, rule);

  // action target checks
  const action = rule.action;
  if (action.type === "calculate") {
    if (!(action.variableId in varTypes)) {
      issues.push({ code: "unknown_variable", message: `Variable "${action.variableId}" no longer exists.` });
    } else {
      const ops = calcOpsForVariableType(varTypes[action.variableId]);
      if (!ops.includes(action.operation)) {
        issues.push({
          code: "invalid_calc_op",
          message: `Operation "${action.operation}" is not valid for the "${varTypes[action.variableId]}" variable.`,
        });
      }
    }
    const v = action.value;
    if (v && typeof v === "object" && v.type === "ref" && !valueSourceIds.has(v.ref)) {
      issues.push({ code: "unknown_calc_ref", message: `Value reference "${v.ref}" no longer exists.` });
    }
  } else if (action.type === "jumpTo" || action.type === "require") {
    const t = action.target;
    if (endIds.has(t)) {
      /* endings are always "ahead" */
    } else if (!(t in idx)) {
      issues.push({ code: "unknown_target", message: `Target "${t}" no longer exists.` });
    } else if (idx[t] <= hostIdx) {
      issues.push({
        code: "backward_jump",
        message: "Target must come after the conditions. Pick a later question or an ending.",
      });
    }
  } else if (action.type === "skip") {
    const targets = Array.isArray(action.target) ? action.target : [action.target];
    if (targets.length === 0) {
      issues.push({ code: "no_target", message: "Pick at least one question to skip." });
    }
    for (const t of targets) {
      if (!(t in idx)) {
        issues.push({ code: "unknown_target", message: `Skip target "${t}" no longer exists.` });
      } else if (idx[t] <= hostIdx) {
        issues.push({
          code: "backward_jump",
          message: "Skip targets must come after the conditions.",
        });
      }
    }
  } else if (action.type === "endSurvey") {
    if (action.endingId !== undefined && !endIds.has(action.endingId)) {
      issues.push({ code: "unknown_target", message: `Ending "${action.endingId}" no longer exists.` });
    }
  }

  // condition checks
  for (const leaf of ruleLeaves(rule)) {
    const ref = leaf.leftQuestionId;
    // A variable condition ref: exempt from position checks; operator validity by var type.
    if (ref in varTypes) {
      const ops = varTypes[ref] === "number" ? NUMERIC_OPS : TEXT_OPS;
      if (!ops.includes(leaf.operator)) {
        issues.push({
          code: "invalid_operator",
          message: `Operator is not valid for the "${varTypes[ref]}" variable.`,
        });
      }
      continue;
    }
    if (!(ref in idx)) {
      issues.push({ code: "unknown_condition_ref", message: `Condition references a missing question.` });
      continue;
    }
    if (idx[ref] > hostIdx) {
      // By construction this should not happen (host = max ref index); guard anyway.
      issues.push({ code: "forward_condition_ref", message: `Condition references a later question.` });
    }
    if (!isOperatorValidForType(qType[ref], leaf.operator)) {
      issues.push({
        code: "invalid_operator",
        message: `Operator is not valid for the "${qType[ref]}" question.`,
      });
    }
  }

  return issues;
}

// --------------------------------------------------------------------------- //
//  Target option helpers (for the action target picker)                       //
// --------------------------------------------------------------------------- //
export interface TargetOption {
  id: string;
  label: string;
  kind: "question" | "ending";
}

/**
 * Valid `jumpTo`/`require` targets for a rule hosted at `hostIdx`: every question
 * AFTER the host (forward-only, no backward_jump) plus every ending. `labelFor`
 * resolves a question's display headline.
 */
export function forwardTargets(
  survey: Survey,
  hostIdx: number,
  labelFor: (q: Question, index: number) => string,
): TargetOption[] {
  const opts: TargetOption[] = [];
  survey.questions.forEach((q, i) => {
    if (i > hostIdx) opts.push({ id: q.id, label: labelFor(q, i), kind: "question" });
  });
  for (const e of survey.endings ?? []) {
    opts.push({ id: e.id, label: endingLabel(e), kind: "ending" });
  }
  return opts;
}

function endingLabel(e: NonNullable<Survey["endings"]>[number]): string {
  const head = e.headline?.default;
  return head && head.trim() !== "" ? `Ending: ${head}` : `Ending (${e.id})`;
}
