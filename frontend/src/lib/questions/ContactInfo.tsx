// ============================================================================
// OpenSurvey — contact-info composite question.
//
// Answer shape (survey-schema.json #/$defs/answerShapes):
//   { firstName?, lastName?, email?, phone?, company? }
//
// Each ENABLED sub-field (config.fields[key].show !== false) renders a labelled
// input; the answer is an object keyed by sub-field. Blank sub-fields are
// dropped from the object; when every field is blank we clear to undefined so
// the engine treats the question as unanswered. email/phone use the matching
// HTML5 input types for browser-level format hints; per-sub-field `required`
// validation lives in answers.ts.
// ============================================================================
import { resolveI18n } from "../i18n";
import type { ContactFieldKey, ContactInfoConfig, Json } from "../types";
import type { QuestionRendererProps } from "./types";

type ContactValue = Partial<Record<ContactFieldKey, string>>;

/** Display order + default label + input type for each contact sub-field. */
const FIELD_ORDER: ContactFieldKey[] = ["firstName", "lastName", "email", "phone", "company"];

const FIELD_META: Record<ContactFieldKey, { label: string; type: string; autoComplete: string }> = {
  firstName: { label: "First name", type: "text", autoComplete: "given-name" },
  lastName: { label: "Last name", type: "text", autoComplete: "family-name" },
  email: { label: "Email", type: "email", autoComplete: "email" },
  phone: { label: "Phone", type: "tel", autoComplete: "tel" },
  company: { label: "Company", type: "text", autoComplete: "organization" },
};

export function ContactInfo({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<ContactInfoConfig, ContactValue>) {
  const fields = config.fields ?? {};
  const current: ContactValue = value && typeof value === "object" ? (value as ContactValue) : {};

  const enabled = FIELD_ORDER.filter((key) => fields[key] && fields[key]!.show !== false);

  function setField(key: ContactFieldKey, text: string) {
    const next: ContactValue = { ...current };
    if (text === "") {
      delete next[key];
    } else {
      next[key] = text;
    }
    onChange(Object.keys(next).length === 0 ? undefined : (next as Json));
  }

  return (
    <div className="space-y-3">
      {enabled.map((key, i) => {
        const meta = FIELD_META[key];
        const sub = fields[key]!;
        const placeholder = resolveI18n(sub.placeholder, lang, defaultLang);
        const fieldId = i === 0 ? inputId : `${inputId}-${key}`;
        return (
          <label key={key} className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">
              {meta.label}
              {sub.required && (
                <span aria-hidden="true" className="ml-1 text-rose-500" title="Required">
                  *
                </span>
              )}
            </span>
            <input
              id={fieldId}
              type={meta.type}
              autoComplete={meta.autoComplete}
              aria-label={meta.label}
              value={current[key] ?? ""}
              placeholder={placeholder || undefined}
              onChange={(e) => setField(key, e.target.value)}
              className={
                "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-base text-slate-900 " +
                "shadow-sm transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
              }
            />
          </label>
        );
      })}
    </div>
  );
}
