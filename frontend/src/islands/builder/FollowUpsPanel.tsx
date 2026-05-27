// ============================================================================
// OpenSurvey — Builder FOLLOW-UPS tab editor (the editor's 4th tab).
//
// Authors survey follow-up automations: send an email when a response is finished,
// optionally only when a simple condition over the answers matches, to a recipient
// derived from the response. Mirrors the target's "Follow-ups" tab using OUR design
// system (hairline cards, brand accents, the same Toggle/Group idioms as the Settings
// tab). Each follow-up edits the SHARED Survey via the surveyModel helpers, so
// BuilderApp's onSurveyChange dirty-tracking + Save drives persistence.
//
// CONDITION (the "simple condition" the brief calls for): a SINGLE condition leaf
// (question/hidden-field + operator + optional value). It is stored as the trigger's
// `condition` (a ConditionGroup leaf) and evaluated server-side by the SAME frozen
// evaluator as logic — so a follow-up condition behaves identically to a logic condition.
// Always-on-finish = no condition.
//
// SUBJECT/BODY are i18n maps (the active editing language writes its key, preserving
// `default`); they may carry recall tokens (#recall:<id>/fallback:<text>#) resolved at
// send time against the response answers — the same recall idiom as headlines.
// ============================================================================
import { useMemo } from "react";

import { resolveI18n } from "../../lib/i18n";
import type {
  ConditionLeaf,
  FollowUp,
  FollowUpRecipient,
  Operator,
  Survey,
} from "../../lib/types";
import {
  addFollowUp,
  getLangText,
  removeFollowUp,
  setFollowUpBody,
  setFollowUpCondition,
  setFollowUpName,
  setFollowUpRecipient,
  setFollowUpSubject,
} from "./surveyModel";

export interface FollowUpsPanelProps {
  survey: Survey;
  onChange: (next: Survey) => void;
  /** Active editing language (i18n subject/body write this key). */
  lang: string;
  /** The survey default-language code (the `default` i18n key pairs with it). */
  defaultLang: string;
}

// The operators a "simple condition" exposes. A focused, type-agnostic set (the server
// enforces operator/type validity on save); covers equality, membership, presence, and
// ordered comparisons — enough for the common "fire only when answer X = Y" follow-up.
const SIMPLE_OPERATORS: Array<{ value: Operator; label: string; needsValue: boolean }> = [
  { value: "equals", label: "equals", needsValue: true },
  { value: "notEquals", label: "does not equal", needsValue: true },
  { value: "contains", label: "contains", needsValue: true },
  { value: "greaterThan", label: "greater than", needsValue: true },
  { value: "lessThan", label: "less than", needsValue: true },
  { value: "isSubmitted", label: "is answered", needsValue: false },
  { value: "isSkipped", label: "is skipped", needsValue: false },
];

export function FollowUpsPanel({ survey, onChange, lang, defaultLang }: FollowUpsPanelProps) {
  const followUps = survey.followUps ?? [];

  // The answer-source options for conditions + responseField recipients: every question +
  // every hidden field id (the valid recall / condition reference namespace).
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

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-ink">Follow-ups</h2>
          <p className="mt-0.5 text-xs text-muted/80">
            Send an email when a response is submitted — to everyone, or only when a condition
            matches.
          </p>
        </div>
        <button
          type="button"
          onClick={() => onChange(addFollowUp(survey))}
          className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand/90"
        >
          + Add follow-up
        </button>
      </div>

      {followUps.length === 0 ? (
        <div className="rounded-xl border border-dashed border-hairline bg-panel px-4 py-10 text-center">
          <p className="text-sm text-muted">No follow-ups yet.</p>
          <p className="mt-1 text-xs text-muted/70">
            Add one to email a notification or a thank-you when a response comes in.
          </p>
        </div>
      ) : (
        <ul className="space-y-4">
          {followUps.map((fu) => (
            <li key={fu.id}>
              <FollowUpCard
                survey={survey}
                followUp={fu}
                sources={sources}
                lang={lang}
                defaultLang={defaultLang}
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
//  one follow-up card                                                         //
// --------------------------------------------------------------------------- //
function FollowUpCard({
  survey,
  followUp,
  sources,
  lang,
  defaultLang,
  onChange,
}: {
  survey: Survey;
  followUp: FollowUp;
  sources: Array<{ id: string; label: string }>;
  lang: string;
  defaultLang: string;
  onChange: (next: Survey) => void;
}) {
  const condition = followUp.trigger.condition;
  // A "simple condition" is a single leaf; a connector-shaped condition (built elsewhere) is
  // shown read-only-ish via its first leaf to avoid clobbering — but the builder only ever
  // writes leaves here.
  const leaf: ConditionLeaf | null =
    condition && !("connector" in condition) ? (condition as ConditionLeaf) : null;
  const conditional = condition != null;
  const recipient = followUp.action.recipient;

  return (
    <div className="rounded-xl border border-hairline bg-white p-4 shadow-sm">
      {/* Header: name + delete */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={followUp.name ?? ""}
          placeholder="Untitled follow-up"
          onChange={(e) => onChange(setFollowUpName(survey, followUp.id, e.target.value))}
          aria-label="Follow-up name"
          className="flex-1 rounded-lg border border-transparent bg-transparent px-1 py-1 text-sm font-medium text-ink hover:border-hairline focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
        />
        <button
          type="button"
          onClick={() => onChange(removeFollowUp(survey, followUp.id))}
          aria-label="Delete follow-up"
          className="rounded-md px-2 py-1 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-50"
        >
          Delete
        </button>
      </div>

      <div className="mt-4 space-y-4">
        {/* Trigger */}
        <FieldGroup label="When">
          <select
            value={conditional ? "conditional" : "always"}
            onChange={(e) => {
              if (e.target.value === "always") {
                onChange(setFollowUpCondition(survey, followUp.id, null));
              } else {
                // Seed a simple leaf against the first available source.
                const first = sources[0]?.id ?? "";
                onChange(
                  setFollowUpCondition(survey, followUp.id, {
                    leftQuestionId: first,
                    operator: "equals",
                    value: "",
                  }),
                );
              }
            }}
            aria-label="Trigger"
            className={selectClass}
          >
            <option value="always">A response is submitted</option>
            <option value="conditional">A response is submitted and…</option>
          </select>

          {conditional && (
            <ConditionRow
              sources={sources}
              leaf={leaf ?? { leftQuestionId: sources[0]?.id ?? "", operator: "equals", value: "" }}
              onChange={(next) => onChange(setFollowUpCondition(survey, followUp.id, next))}
            />
          )}
        </FieldGroup>

        {/* Recipient */}
        <FieldGroup label="Send to">
          <RecipientEditor
            recipient={recipient}
            sources={sources}
            onChange={(next) => onChange(setFollowUpRecipient(survey, followUp.id, next))}
          />
        </FieldGroup>

        {/* Subject */}
        <FieldGroup label="Subject">
          <input
            type="text"
            value={getLangText(followUp.action.subject, lang, defaultLang)}
            onChange={(e) =>
              onChange(setFollowUpSubject(survey, followUp.id, lang, e.target.value, defaultLang))
            }
            aria-label="Email subject"
            placeholder="Subject line"
            className={inputClass}
          />
        </FieldGroup>

        {/* Body */}
        <FieldGroup label="Body">
          <textarea
            value={getLangText(followUp.action.body, lang, defaultLang)}
            onChange={(e) =>
              onChange(setFollowUpBody(survey, followUp.id, lang, e.target.value, defaultLang))
            }
            aria-label="Email body"
            rows={4}
            placeholder="<p>Your message…</p>"
            className={`${inputClass} font-mono text-xs`}
          />
          <p className="mt-1 text-xs text-muted/70">
            HTML allowed. Use <code>#recall:&lt;id&gt;/fallback:&lt;text&gt;#</code> to insert an
            answer (e.g. a name or email).
          </p>
        </FieldGroup>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  recipient editor (discriminated)                                           //
// --------------------------------------------------------------------------- //
function RecipientEditor({
  recipient,
  sources,
  onChange,
}: {
  recipient: FollowUpRecipient;
  sources: Array<{ id: string; label: string }>;
  onChange: (next: FollowUpRecipient) => void;
}) {
  return (
    <div className="space-y-2">
      <select
        value={recipient.type}
        onChange={(e) => {
          const t = e.target.value as FollowUpRecipient["type"];
          if (t === "contact") onChange({ type: "contact" });
          else if (t === "fixedEmail") onChange({ type: "fixedEmail", email: "" });
          else onChange({ type: "responseField", questionId: sources[0]?.id ?? "" });
        }}
        aria-label="Recipient type"
        className={selectClass}
      >
        <option value="responseField">The respondent (an answer)</option>
        <option value="contact">The associated contact</option>
        <option value="fixedEmail">A fixed email address</option>
      </select>

      {recipient.type === "fixedEmail" && (
        <div>
          <input
            type="email"
            value={recipient.email}
            onChange={(e) => onChange({ type: "fixedEmail", email: e.target.value })}
            aria-label="Recipient email"
            aria-invalid={recipient.email.trim() === ""}
            placeholder="ops@example.com"
            className={
              recipient.email.trim() === "" ? `${inputClass} !border-red-400` : inputClass
            }
          />
          {recipient.email.trim() === "" && (
            <p className="mt-1 text-xs text-red-600">
              Enter a recipient email address — it’s required before this follow-up can be saved.
            </p>
          )}
        </div>
      )}

      {recipient.type === "responseField" && (
        <select
          value={recipient.questionId}
          onChange={(e) => onChange({ type: "responseField", questionId: e.target.value })}
          aria-label="Recipient question"
          className={selectClass}
        >
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      )}

      {recipient.type === "contact" && (
        <p className="text-xs text-muted/70">
          Uses the contact's <code>email</code> attribute (from a personalized link). If the
          response has no associated contact email, the email is skipped.
        </p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  single condition leaf editor (the "simple condition")                      //
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
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-panel p-2.5">
      <select
        value={leaf.leftQuestionId}
        onChange={(e) => onChange({ ...leaf, leftQuestionId: e.target.value })}
        aria-label="Condition question"
        className={selectClass}
      >
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
          value={typeof leaf.value === "string" || typeof leaf.value === "number" ? String(leaf.value) : ""}
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
