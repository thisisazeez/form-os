// ============================================================================
// OpenSurvey — Builder QUOTAS section (Settings tab).
//
// Authors response quotas: cap how many FINISHED responses matching a condition are accepted,
// then route over-quota respondents to an ending (or a built-in 'quota full' screen). Mirrors
// the target's quota feature using OUR design system (the same hairline cards + Toggle/Group
// idioms as the Settings tab's HiddenFields/Variables panels, and the SAME condition-row idiom
// as the Follow-ups tab). Each quota edits the SHARED Survey via the surveyModel helpers, so
// BuilderApp's onSurveyChange dirty-tracking + Save drives persistence.
//
// CONDITION: a SINGLE condition leaf (question/hidden-field + operator + optional value), stored
// as the quota's `condition` (a ConditionGroup leaf) and evaluated SERVER-SIDE at finalize by the
// SAME frozen evaluator as logic — so a quota condition behaves identically to a logic condition.
//
// NAMES are internal (no i18n / no multi-language) — quota names never reach a respondent.
// ============================================================================
import { useMemo } from "react";

import { resolveI18n } from "../../lib/i18n";
import type { ConditionLeaf, Operator, Quota, Survey } from "../../lib/types";
import {
  addQuota,
  removeQuota,
  setQuotaCondition,
  setQuotaEndingId,
  setQuotaLimit,
  setQuotaName,
} from "./surveyModel";

export interface QuotasPanelProps {
  survey: Survey;
  onChange: (next: Survey) => void;
  /** Active editing language (used only to LABEL question/ending sources, not to write text). */
  lang: string;
  /** The survey default-language code (the `default` i18n key pairs with it). */
  defaultLang: string;
}

// The operators a quota "simple condition" exposes — the SAME focused set the Follow-ups tab uses
// (the server enforces operator/type validity on save). Covers equality, membership, presence,
// and ordered comparisons — enough for the common "count only responses where answer X = Y" quota.
const SIMPLE_OPERATORS: Array<{ value: Operator; label: string; needsValue: boolean }> = [
  { value: "equals", label: "equals", needsValue: true },
  { value: "notEquals", label: "does not equal", needsValue: true },
  { value: "contains", label: "contains", needsValue: true },
  { value: "greaterThan", label: "greater than", needsValue: true },
  { value: "lessThan", label: "less than", needsValue: true },
  { value: "isSubmitted", label: "is answered", needsValue: false },
  { value: "isSkipped", label: "is skipped", needsValue: false },
];

export function QuotasPanel({ survey, onChange, lang, defaultLang }: QuotasPanelProps) {
  const quotas = survey.quotas ?? [];

  // The answer-source options for conditions: every question + every hidden field id (the valid
  // condition reference namespace — quotas evaluate against Response.data + hidden fields).
  const sources = useMemo(() => {
    const out: Array<{ id: string; label: string }> = [];
    for (const q of survey.questions) {
      out.push({ id: q.id, label: resolveI18n(q.headline, lang, defaultLang) || q.id });
    }
    for (const hf of survey.hiddenFields ?? []) {
      out.push({ id: hf.id, label: `${hf.name?.trim() || hf.id} (hidden field)` });
    }
    return out;
  }, [survey.questions, survey.hiddenFields, lang, defaultLang]);

  // The over-quota ending options: every declared ending (label = its headline or id).
  const endings = useMemo(
    () =>
      (survey.endings ?? []).map((e) => ({
        id: e.id,
        label: resolveI18n(e.headline, lang, defaultLang) || e.id,
      })),
    [survey.endings, lang, defaultLang],
  );

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-ink">Quotas</h2>
          <p className="mt-0.5 text-xs text-muted/80">
            Cap how many responses matching a condition you accept. Once a quota is full, further
            matching respondents see a “quota full” screen (or a target ending you choose).
          </p>
        </div>
        <button
          type="button"
          onClick={() => onChange(addQuota(survey))}
          className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand/90"
        >
          + Add quota
        </button>
      </div>

      {quotas.length === 0 ? (
        <div className="rounded-xl border border-dashed border-hairline bg-panel px-4 py-10 text-center">
          <p className="text-sm text-muted">No quotas yet.</p>
          <p className="mt-1 text-xs text-muted/70">
            Add one to limit how many matching responses you collect.
          </p>
        </div>
      ) : (
        <ul className="space-y-4">
          {quotas.map((quota) => (
            <li key={quota.id}>
              <QuotaCard
                survey={survey}
                quota={quota}
                sources={sources}
                endings={endings}
                onChange={onChange}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  one quota card                                                             //
// --------------------------------------------------------------------------- //
function QuotaCard({
  survey,
  quota,
  sources,
  endings,
  onChange,
}: {
  survey: Survey;
  quota: Quota;
  sources: Array<{ id: string; label: string }>;
  endings: Array<{ id: string; label: string }>;
  onChange: (next: Survey) => void;
}) {
  // A "simple condition" is a single leaf; a connector-shaped condition (built elsewhere) falls
  // back to a fresh leaf for editing here — the builder only ever writes leaves in this panel.
  const leaf: ConditionLeaf =
    quota.condition && !("connector" in quota.condition)
      ? (quota.condition as ConditionLeaf)
      : { leftQuestionId: sources[0]?.id ?? "", operator: "equals", value: "" };

  return (
    <div className="rounded-xl border border-hairline bg-white p-4 shadow-sm">
      {/* Header: name + delete */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={quota.name ?? ""}
          placeholder="Untitled quota"
          onChange={(e) => onChange(setQuotaName(survey, quota.id, e.target.value))}
          aria-label="Quota name"
          className="flex-1 rounded-lg border border-transparent bg-transparent px-1 py-1 text-sm font-medium text-ink hover:border-hairline focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
        />
        <button
          type="button"
          onClick={() => onChange(removeQuota(survey, quota.id))}
          aria-label="Delete quota"
          className="rounded-md px-2 py-1 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-50"
        >
          Delete
        </button>
      </div>

      <div className="mt-4 space-y-4">
        {/* Limit */}
        <FieldGroup label="Limit">
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              step={1}
              value={quota.limit}
              onChange={(e) => onChange(setQuotaLimit(survey, quota.id, e.target.value))}
              aria-label="Quota limit"
              className={`${inputClass} max-w-[8rem]`}
            />
            <span className="text-xs text-muted/80">matching responses accepted</span>
          </div>
        </FieldGroup>

        {/* Condition */}
        <FieldGroup label="When a response matches">
          <ConditionRow
            sources={sources}
            leaf={leaf}
            onChange={(next) => onChange(setQuotaCondition(survey, quota.id, next))}
          />
        </FieldGroup>

        {/* Over-quota ending */}
        <FieldGroup label="When full, show">
          <select
            value={quota.endingId ?? ""}
            onChange={(e) =>
              onChange(setQuotaEndingId(survey, quota.id, e.target.value || undefined))
            }
            aria-label="Over-quota ending"
            className={selectClass}
          >
            <option value="">Built-in “quota full” screen</option>
            {endings.map((e) => (
              <option key={e.id} value={e.id}>
                {e.label}
              </option>
            ))}
          </select>
        </FieldGroup>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  single condition leaf editor (the "simple condition" — mirrors FollowUpsPanel)
// --------------------------------------------------------------------------- //
function ConditionRow({
  sources,
  leaf,
  onChange,
}: {
  sources: Array<{ id: string; label: string }>;
  leaf: ConditionLeaf;
  onChange: (next: ConditionLeaf) => void;
}) {
  const opMeta = SIMPLE_OPERATORS.find((o) => o.value === leaf.operator);
  const needsValue = opMeta?.needsValue ?? true;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-panel p-2.5">
      <select
        value={leaf.leftQuestionId}
        onChange={(e) => onChange({ ...leaf, leftQuestionId: e.target.value })}
        aria-label="Condition question"
        className={selectClass}
      >
        {sources.length === 0 && <option value="">(add a question first)</option>}
        {sources.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </select>
      <select
        value={leaf.operator}
        onChange={(e) => {
          const op = e.target.value as Operator;
          const meta = SIMPLE_OPERATORS.find((o) => o.value === op);
          // Drop the value for presence operators; keep it otherwise.
          if (meta && !meta.needsValue) {
            const { value: _drop, ...rest } = leaf;
            onChange({ ...rest, operator: op });
          } else {
            onChange({ ...leaf, operator: op, value: leaf.value ?? "" });
          }
        }}
        aria-label="Condition operator"
        className={selectClass}
      >
        {SIMPLE_OPERATORS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {needsValue && (
        <input
          type="text"
          value={
            typeof leaf.value === "string" || typeof leaf.value === "number"
              ? String(leaf.value)
              : ""
          }
          onChange={(e) => onChange({ ...leaf, value: e.target.value })}
          aria-label="Condition value"
          placeholder="value"
          className={`${inputClass} max-w-[10rem]`}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  building blocks                                                            //
// --------------------------------------------------------------------------- //
function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted">
        {label}
      </span>
      {children}
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";
const selectClass = inputClass;
