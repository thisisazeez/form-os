// ============================================================================
// onForm — Logic RULE CARD (one IF/THEN rule editor).
//
// Renders + edits a single LogicRule as the mockup does:
//
//   ⠿  IF  [question ▾] [operator ▾] [value]            🗑
//          AND/OR
//          [question ▾] [operator ▾] [value]   ✕
//          + Add condition
//      THEN [action ▾] [target ▾]
//
// Every edit produces a fresh LogicRule and calls `onChange(rule)`; the parent
// re-hosts it (logicModel.setRule) so the host invariant holds, then marks the
// survey dirty. Inline issues (from validateRuleAgainstSurvey) surface when a
// re-host invalidates the action target — the UI then offers a re-pick.
// ============================================================================
import { resolveI18n } from "../../../lib/i18n";
import type {
  Action,
  CalcValue,
  ConditionLeaf,
  LogicRule,
  Operator,
  Question,
  QuestionType,
  Survey,
} from "../../../lib/types";
import {
  ACTION_LABEL,
  type ActionType,
  CALC_OP_LABEL,
  calcOpsForVariableType,
  type FlatCondition,
  firstForwardAction,
  forwardTargets,
  fromFlatCondition,
  hostIndexForRule,
  OPERATOR_LABEL,
  OPERATOR_VALIDITY,
  setLeafArrayValue,
  setLeafOperator,
  setLeafQuestion,
  setLeafValue,
  toFlatCondition,
  validateRuleAgainstSurvey,
  valueKind,
  variableTypeMap,
} from "./logicModel";

// --------------------------------------------------------------------------- //
//  Shared display helpers                                                     //
// --------------------------------------------------------------------------- //
/** "Q3. What's your name?" — the 1-based position + the default headline. */
function questionLabel(survey: Survey, q: Question, index: number): string {
  const lang = (survey.languages ?? []).find((l) => l.default)?.code ?? "default";
  const head = resolveI18n(q.headline, lang, lang) || "(untitled)";
  return `Q${index + 1}. ${head}`;
}

function shortQuestionLabel(survey: Survey, q: Question, index: number): string {
  return questionLabel(survey, q, index);
}

interface RuleCardProps {
  survey: Survey;
  rule: LogicRule;
  ruleNumber: number;
  total: number;
  onChange: (rule: LogicRule) => void;
  onDelete: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}

export function RuleCard({
  survey,
  rule,
  ruleNumber,
  total,
  onChange,
  onDelete,
  onMoveUp,
  onMoveDown,
}: RuleCardProps) {
  const flat = toFlatCondition(rule.condition);
  const issues = validateRuleAgainstSurvey(survey, rule);

  // Replace the rule's condition with an edited flat shape and emit.
  const emitCondition = (next: FlatCondition) => {
    onChange({ ...rule, condition: fromFlatCondition(next) });
  };

  const setLeaf = (i: number, leaf: ConditionLeaf) => {
    const leaves = flat.leaves.map((l, j) => (j === i ? leaf : l));
    emitCondition({ ...flat, leaves });
  };

  const addCondition = () => {
    // New condition references questions[0] with its default operator (valid by
    // construction; setLeafQuestion normalizes the value to the op's value-kind).
    // The parent re-host keeps the host = max ref index.
    const firstQ = survey.questions[0];
    if (!firstQ) return;
    const leaf = setLeafQuestion(firstQ.id, firstQ.type);
    emitCondition({ ...flat, leaves: [...flat.leaves, leaf] });
  };

  const deleteCondition = (i: number) => {
    if (flat.leaves.length <= 1) return; // keep at least one condition
    const leaves = flat.leaves.filter((_, j) => j !== i);
    emitCondition({ ...flat, leaves });
  };

  const setConnector = (connector: "and" | "or") => {
    emitCondition({ ...flat, connector });
  };

  return (
    <div
      data-testid="rule-card"
      className="rounded-xl border border-hairline bg-white p-4 shadow-sm"
    >
      <div className="flex items-start gap-3">
        {/* reorder handle */}
        <div className="flex flex-col pt-1.5 text-muted" aria-hidden="false">
          <button
            type="button"
            onClick={onMoveUp}
            disabled={ruleNumber === 1}
            aria-label={`Move rule ${ruleNumber} up`}
            className="leading-none hover:text-ink disabled:opacity-30"
          >
            ▲
          </button>
          <button
            type="button"
            onClick={onMoveDown}
            disabled={ruleNumber === total}
            aria-label={`Move rule ${ruleNumber} down`}
            className="leading-none hover:text-ink disabled:opacity-30"
          >
            ▼
          </button>
        </div>

        <div className="min-w-0 flex-1 space-y-3">
          {/* IF block */}
          {flat.leaves.map((leaf, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <ConnectorBadge
                index={i}
                connector={flat.connector}
                onConnectorChange={setConnector}
              />
              <ConditionLeafEditor
                survey={survey}
                leaf={leaf}
                onChange={(l) => setLeaf(i, l)}
              />
              {flat.leaves.length > 1 && (
                <button
                  type="button"
                  onClick={() => deleteCondition(i)}
                  aria-label={`Remove condition ${i + 1}`}
                  className="shrink-0 rounded p-1 text-muted hover:bg-rose-50 hover:text-rose-600"
                >
                  ✕
                </button>
              )}
            </div>
          ))}

          <button
            type="button"
            onClick={addCondition}
            className="rounded-md text-sm font-medium text-brand hover:text-brand-hover"
          >
            + Add condition
          </button>

          <div className="border-t border-hairline pt-3">
            <ThenBlock survey={survey} rule={rule} onChange={onChange} />
          </div>

          {issues.length > 0 && (
            <ul className="space-y-1 rounded-lg bg-rose-50 px-3 py-2" data-testid="rule-issues">
              {issues.map((issue, i) => (
                <li key={i} className="text-xs font-medium text-rose-700">
                  {issue.message}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* delete rule */}
        <button
          type="button"
          onClick={onDelete}
          aria-label={`Delete rule ${ruleNumber}`}
          className="shrink-0 rounded-lg p-1.5 text-muted transition-colors hover:bg-rose-50 hover:text-rose-600"
          title="Delete rule"
        >
          <span aria-hidden="true">🗑</span>
        </button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Connector badge (IF / AND / OR)                                            //
// --------------------------------------------------------------------------- //
function ConnectorBadge({
  index,
  connector,
  onConnectorChange,
}: {
  index: number;
  connector: "and" | "or";
  onConnectorChange: (c: "and" | "or") => void;
}) {
  if (index === 0) {
    return (
      <span className="inline-flex shrink-0 items-center rounded bg-panel px-2 py-1 text-xs font-semibold uppercase tracking-wide text-ink">
        IF
      </span>
    );
  }
  // The 2nd+ condition shows the (editable) connector; changing it flips the whole
  // group's connector (the flat model has ONE connector for the IF block).
  return (
    <select
      value={connector}
      onChange={(e) => onConnectorChange(e.target.value as "and" | "or")}
      aria-label="Condition connector"
      className="shrink-0 rounded bg-panel px-2 py-1 text-xs font-semibold uppercase tracking-wide text-ink focus:outline-none focus:ring-1 focus:ring-brand"
    >
      <option value="and">AND</option>
      <option value="or">OR</option>
    </select>
  );
}

// --------------------------------------------------------------------------- //
//  Condition leaf editor: [question ▾] [operator ▾] [value]                   //
// --------------------------------------------------------------------------- //
function ConditionLeafEditor({
  survey,
  leaf,
  onChange,
}: {
  survey: Survey;
  leaf: ConditionLeaf;
  onChange: (leaf: ConditionLeaf) => void;
}) {
  const varMap = variableTypeMap(survey);
  const refVar = varMap[leaf.leftQuestionId];
  const refQ = survey.questions.find((q) => q.id === leaf.leftQuestionId);
  // A variable condition source maps to a number/text "pseudo-type" for the operator picker
  // (number -> the rating/number op family, text -> the openText op family) — reusing the
  // existing OPERATOR_VALIDITY rows so the builder offers exactly the operators the validator
  // accepts for that variable type.
  const refType: QuestionType = refVar
    ? refVar === "number"
      ? "number"
      : "openText"
    : refQ?.type ?? "openText";
  const validOps = OPERATOR_VALIDITY[refType] ?? [];
  const kind = valueKind(refType, leaf.operator);

  const sourceOptions: Array<{ value: string; label: string }> = [
    ...survey.questions.map((q, i) => ({ value: q.id, label: shortQuestionLabel(survey, q, i) })),
    ...(survey.variables ?? []).map((v) => ({ value: v.id, label: `Variable: ${v.name}` })),
  ];

  return (
    <>
      {/* source picker (question or variable) */}
      <Select
        ariaLabel="Condition question"
        value={leaf.leftQuestionId}
        onChange={(id) => {
          const vt = varMap[id];
          // A variable maps to its number/text pseudo-type so setLeafQuestion picks a valid op.
          const pseudoType: QuestionType = vt
            ? vt === "number"
              ? "number"
              : "openText"
            : survey.questions.find((x) => x.id === id)?.type ?? "openText";
          onChange(setLeafQuestion(id, pseudoType));
        }}
        options={sourceOptions}
      />

      {/* operator picker (constrained to the ref type's valid operators) */}
      <Select
        ariaLabel="Condition operator"
        value={leaf.operator}
        onChange={(op) => onChange(setLeafOperator(leaf, op as Operator, refType))}
        options={validOps.map((op) => ({ value: op, label: OPERATOR_LABEL[op] }))}
      />

      {/* value editor (shape depends on the type+operator) */}
      <ValueEditor refQ={refQ} kind={kind} leaf={leaf} onChange={onChange} />
    </>
  );
}

function ValueEditor({
  refQ,
  kind,
  leaf,
  onChange,
}: {
  refQ: Question | undefined;
  kind: ReturnType<typeof valueKind>;
  leaf: ConditionLeaf;
  onChange: (leaf: ConditionLeaf) => void;
}) {
  if (kind === "none") return null;

  if (kind === "number") {
    const v = typeof leaf.value === "number" ? leaf.value : "";
    return (
      <input
        type="number"
        aria-label="Condition value"
        value={v}
        onChange={(e) => onChange(setLeafValue(leaf, e.target.value === "" ? 0 : Number(e.target.value)))}
        className={valueInputClass}
      />
    );
  }

  if (kind === "date") {
    const v = typeof leaf.value === "string" ? leaf.value : "";
    return (
      <input
        type="date"
        aria-label="Condition value"
        value={v}
        onChange={(e) => onChange(setLeafValue(leaf, e.target.value))}
        className={valueInputClass}
      />
    );
  }

  if (kind === "singleChoice") {
    const choices = choicesOf(refQ);
    const v = typeof leaf.value === "string" ? leaf.value : "";
    return (
      <Select
        ariaLabel="Condition value"
        value={v}
        onChange={(cid) => onChange(setLeafValue(leaf, cid))}
        options={[
          { value: "", label: "Select…" },
          ...choices.map((c) => ({ value: c.id, label: c.label })),
        ]}
      />
    );
  }

  if (kind === "multiChoice") {
    const choices = choicesOf(refQ);
    const selected = new Set(Array.isArray(leaf.value) ? leaf.value.map(String) : []);
    return (
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Condition value">
        {choices.length === 0 && <span className="text-xs text-muted">No choices</span>}
        {choices.map((c) => {
          const on = selected.has(c.id);
          return (
            <button
              key={c.id}
              type="button"
              aria-pressed={on}
              onClick={() => {
                const next = new Set(selected);
                if (on) next.delete(c.id);
                else next.add(c.id);
                onChange(setLeafArrayValue(leaf, [...next]));
              }}
              className={
                "rounded-full px-2.5 py-1 text-xs font-medium transition-colors " +
                (on ? "bg-brand text-white" : "bg-panel text-muted hover:bg-brand/10")
              }
            >
              {c.label}
            </button>
          );
        })}
      </div>
    );
  }

  // text
  const v = typeof leaf.value === "string" ? leaf.value : leaf.value == null ? "" : String(leaf.value);
  return (
    <input
      type="text"
      aria-label="Condition value"
      value={v}
      placeholder="value"
      onChange={(e) => onChange(setLeafValue(leaf, e.target.value))}
      className={valueInputClass}
    />
  );
}

/** Resolve a question's choices (singleSelect/multiSelect) for value pickers. */
function choicesOf(q: Question | undefined): Array<{ id: string; label: string }> {
  if (!q) return [];
  const cfg = (q.config ?? {}) as { choices?: Array<{ id: string; label?: { default?: string } }> };
  const raw = Array.isArray(cfg.choices) ? cfg.choices : [];
  return raw.map((c) => ({ id: c.id, label: c.label?.default ?? c.id }));
}

// --------------------------------------------------------------------------- //
//  THEN block: [action ▾] [target ▾]                                          //
// --------------------------------------------------------------------------- //
function ThenBlock({
  survey,
  rule,
  onChange,
}: {
  survey: Survey;
  rule: LogicRule;
  onChange: (rule: LogicRule) => void;
}) {
  const hostIdx = hostIndexForRule(survey, rule);
  const targets = forwardTargets(survey, hostIdx, (q, i) => shortQuestionLabel(survey, q, i));
  const action = rule.action;

  const setActionType = (type: ActionType) => {
    onChange({ ...rule, action: actionForType(type, survey, hostIdx) });
  };

  const setJumpTarget = (target: string) => {
    if (action.type === "endSurvey") {
      onChange({ ...rule, action: { type: "endSurvey", endingId: target || undefined } });
    } else {
      onChange({ ...rule, action: { ...action, target } as Action });
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="inline-flex shrink-0 items-center rounded bg-panel px-2 py-1 text-xs font-semibold uppercase tracking-wide text-ink">
        THEN
      </span>

      <Select
        ariaLabel="Action"
        value={action.type}
        onChange={(t) => setActionType(t as ActionType)}
        options={(
          [
            "jumpTo",
            "skip",
            "endSurvey",
            "require",
            // calculate is offered only when the survey has at least one variable to mutate.
            ...((survey.variables ?? []).length > 0 ? (["calculate"] as ActionType[]) : []),
          ] as ActionType[]
        ).map((t) => ({
          value: t,
          label: ACTION_LABEL[t],
        }))}
      />

      {/* calculate: variable + operation + value (literal | ref) */}
      {action.type === "calculate" && (
        <CalculateEditor survey={survey} rule={rule} action={action} onChange={onChange} />
      )}

      {/* jumpTo / require: single forward target (question or ending) */}
      {(action.type === "jumpTo" || action.type === "require") && (
        <Select
          ariaLabel="Action target"
          value={action.target}
          onChange={setJumpTarget}
          options={
            targets.length > 0
              ? targets.map((t) => ({ value: t.id, label: t.label }))
              : [{ value: "", label: "No questions after this — add one" }]
          }
        />
      )}

      {/* skip: one or more forward questions (choice chips) */}
      {action.type === "skip" && (
        <SkipTargets survey={survey} hostIdx={hostIdx} action={action} onChange={onChange} rule={rule} />
      )}

      {/* endSurvey: optional ending picker (defaults to first / end-of-survey) */}
      {action.type === "endSurvey" && (
        <Select
          ariaLabel="Ending"
          value={action.endingId ?? ""}
          onChange={setJumpTarget}
          options={[
            { value: "", label: "Default ending" },
            ...(survey.endings ?? []).map((e) => ({
              value: e.id,
              label: e.headline?.default ? `Ending: ${e.headline.default}` : `Ending (${e.id})`,
            })),
          ]}
        />
      )}
    </div>
  );
}

function SkipTargets({
  survey,
  hostIdx,
  action,
  rule,
  onChange,
}: {
  survey: Survey;
  hostIdx: number;
  action: Extract<Action, { type: "skip" }>;
  rule: LogicRule;
  onChange: (rule: LogicRule) => void;
}) {
  const targets = forwardTargets(survey, hostIdx, (q, i) => shortQuestionLabel(survey, q, i)).filter(
    (t) => t.kind === "question",
  );
  const selected = new Set(Array.isArray(action.target) ? action.target : [action.target].filter(Boolean));

  if (targets.length === 0) {
    return <span className="text-xs text-muted">No questions after this to skip.</span>;
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Skip targets">
      {targets.map((t) => {
        const on = selected.has(t.id);
        return (
          <button
            key={t.id}
            type="button"
            aria-pressed={on}
            onClick={() => {
              const next = new Set(selected);
              if (on) {
                // Never let the skip set go empty (schema requires minItems:1 for an
                // array target; a 0-target skip is meaningless) — ignore the deselect.
                if (next.size <= 1) return;
                next.delete(t.id);
              } else {
                next.add(t.id);
              }
              const arr = [...next];
              onChange({
                ...rule,
                action: { type: "skip", target: arr.length === 1 ? arr[0] : arr },
              });
            }}
            className={
              "rounded-full px-2.5 py-1 text-xs font-medium transition-colors " +
              (on ? "bg-brand text-white" : "bg-panel text-muted hover:bg-brand/10")
            }
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Calculate editor: [variable ▾] [operation ▾] [literal | ref]               //
// --------------------------------------------------------------------------- //
function CalculateEditor({
  survey,
  rule,
  action,
  onChange,
}: {
  survey: Survey;
  rule: LogicRule;
  action: Extract<Action, { type: "calculate" }>;
  onChange: (rule: LogicRule) => void;
}) {
  const variables = survey.variables ?? [];
  const varType = variableTypeMap(survey)[action.variableId] ?? "text";
  const ops = calcOpsForVariableType(varType);
  // value sources for a ref: questions + variables + hidden fields.
  const refOptions: Array<{ value: string; label: string }> = [
    ...survey.questions.map((q, i) => ({ value: q.id, label: shortQuestionLabel(survey, q, i) })),
    ...variables.map((v) => ({ value: v.id, label: `Variable: ${v.name}` })),
    ...(survey.hiddenFields ?? []).map((f) => ({
      value: f.id,
      label: `Hidden: ${f.name ?? f.id}`,
    })),
  ];

  const isRef = action.value && typeof action.value === "object" && action.value.type === "ref";
  const literalValue =
    action.value && action.value.type === "literal" ? action.value.value : "";
  const refValue = isRef ? (action.value as { type: "ref"; ref: string }).ref : "";

  const setAction = (next: Extract<Action, { type: "calculate" }>) => onChange({ ...rule, action: next });

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="calculate-editor">
      {/* variable picker */}
      <Select
        ariaLabel="Calculate variable"
        value={action.variableId}
        onChange={(variableId) => {
          // When switching variable, snap the operation to one valid for the new type.
          const newType = variableTypeMap(survey)[variableId] ?? "text";
          const newOps = calcOpsForVariableType(newType);
          const operation = newOps.includes(action.operation) ? action.operation : newOps[0];
          setAction({ ...action, variableId, operation: operation as typeof action.operation });
        }}
        options={
          variables.length > 0
            ? variables.map((v) => ({ value: v.id, label: v.name }))
            : [{ value: "", label: "No variables — add one in the Variables panel" }]
        }
      />

      {/* operation picker (constrained to the variable type) */}
      <Select
        ariaLabel="Calculate operation"
        value={action.operation}
        onChange={(op) => setAction({ ...action, operation: op as typeof action.operation })}
        options={ops.map((op) => ({ value: op, label: CALC_OP_LABEL[op] ?? op }))}
      />

      {/* value mode toggle: literal vs reference */}
      <Select
        ariaLabel="Calculate value mode"
        value={isRef ? "ref" : "literal"}
        onChange={(mode) => {
          if (mode === "ref") {
            setAction({ ...action, value: { type: "ref", ref: refOptions[0]?.value ?? "" } });
          } else {
            setAction({
              ...action,
              value: { type: "literal", value: varType === "number" ? 0 : "" },
            });
          }
        }}
        options={[
          { value: "literal", label: "a value" },
          { value: "ref", label: "an answer / variable" },
        ]}
      />

      {/* literal or ref value editor */}
      {isRef ? (
        <Select
          ariaLabel="Calculate value reference"
          value={refValue}
          onChange={(ref) => setAction({ ...action, value: { type: "ref", ref } })}
          options={
            refOptions.length > 0
              ? refOptions
              : [{ value: "", label: "No sources available" }]
          }
        />
      ) : varType === "number" ? (
        <input
          type="number"
          aria-label="Calculate literal value"
          value={typeof literalValue === "number" ? literalValue : ""}
          onChange={(e) =>
            setAction({
              ...action,
              value: { type: "literal", value: e.target.value === "" ? 0 : Number(e.target.value) },
            })
          }
          className={valueInputClass}
        />
      ) : (
        <input
          type="text"
          aria-label="Calculate literal value"
          value={typeof literalValue === "string" ? literalValue : String(literalValue)}
          placeholder="text"
          onChange={(e) =>
            setAction({ ...action, value: { type: "literal", value: e.target.value } })
          }
          className={valueInputClass}
        />
      )}
    </div>
  );
}

/**
 * Build a fresh action of `type` with a sensible, SCHEMA-VALID default target:
 *   - endSurvey → no target (defaults to the first ending / end-of-survey).
 *   - skip      → the first question after the host. If NONE exists, fall back to
 *                 endSurvey (never emit an empty `target: []`, which violates the
 *                 schema's `minItems: 1` for array skip targets).
 *   - jumpTo    → next question, else first ending, else endSurvey (firstForwardAction).
 *   - require   → the next question (require can ONLY target a later QUESTION, not an
 *                 ending/end). If none exists, emit `target: ""` so the inline
 *                 validator + Save banner flag it (`unknown_target`) rather than
 *                 silently doing the wrong thing.
 *   - calculate → the first declared variable + a type-valid operation + a 0/"" literal.
 *                 If no variable exists the picker shows an empty-state; the value/op are
 *                 still schema-shaped so Save's validator flags the unknown variable.
 */
function actionForType(type: ActionType, survey: Survey, hostIdx: number): Action {
  if (type === "endSurvey") return { type: "endSurvey" };

  const firstAfter = survey.questions[hostIdx + 1];

  if (type === "calculate") {
    const v = (survey.variables ?? [])[0];
    const vtype: "number" | "text" = v?.type === "number" ? "number" : "text";
    const operation = calcOpsForVariableType(vtype)[0] as
      | "assign"
      | "add"
      | "subtract"
      | "multiply"
      | "divide"
      | "concat";
    const value: CalcValue = { type: "literal", value: vtype === "number" ? 0 : "" };
    return { type: "calculate", variableId: v?.id ?? "", operation, value };
  }

  if (type === "skip") {
    if (firstAfter) return { type: "skip", target: firstAfter.id };
    return { type: "endSurvey" }; // nothing to skip — end the survey instead
  }

  if (type === "jumpTo") {
    // May resolve to a jumpTo (question/ending) OR an endSurvey when nothing follows.
    return firstForwardAction(survey, hostIdx);
  }

  // require: must target a later QUESTION.
  if (firstAfter) return { type: "require", target: firstAfter.id };
  return { type: "require", target: "" }; // no later question → flagged by the validator
}

// --------------------------------------------------------------------------- //
//  Small primitives                                                           //
// --------------------------------------------------------------------------- //
const selectClass =
  "shrink-0 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink " +
  "shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";

const valueInputClass =
  "w-24 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink " +
  "shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";

function Select({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (v: string) => void;
  ariaLabel: string;
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={selectClass}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
