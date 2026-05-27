// ============================================================================
// OpenSurvey — address composite question.
//
// Answer shape (survey-schema.json #/$defs/answerShapes):
//   { addressLine1?, addressLine2?, city?, state?, zip?, country? }
//
// Mirrors the ContactInfo pattern: each ENABLED sub-field (config.fields[key].show
// !== false) renders a labelled text input; the answer is an object keyed by
// sub-field. Blank sub-fields are dropped from the object; when every field is
// blank we clear to undefined so the engine treats the question as unanswered.
// Postal code / region are NOT format-validated (they vary by country); per-
// sub-field `required` validation lives in answers.ts.
// ============================================================================
import { resolveI18n } from "../i18n";
import type { AddressConfig, AddressFieldKey, Json } from "../types";
import type { QuestionRendererProps } from "./types";

type AddressValue = Partial<Record<AddressFieldKey, string>>;

/** Display order + default label + autocomplete hint for each address sub-field. */
const FIELD_ORDER: AddressFieldKey[] = [
  "addressLine1",
  "addressLine2",
  "city",
  "state",
  "zip",
  "country",
];

const FIELD_META: Record<AddressFieldKey, { label: string; autoComplete: string }> = {
  addressLine1: { label: "Address line 1", autoComplete: "address-line1" },
  addressLine2: { label: "Address line 2", autoComplete: "address-line2" },
  city: { label: "City", autoComplete: "address-level2" },
  state: { label: "State / Region", autoComplete: "address-level1" },
  zip: { label: "ZIP / Postal code", autoComplete: "postal-code" },
  country: { label: "Country", autoComplete: "country-name" },
};

export function Address({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<AddressConfig, AddressValue>) {
  const fields = config.fields ?? {};
  const current: AddressValue = value && typeof value === "object" ? (value as AddressValue) : {};

  const enabled = FIELD_ORDER.filter((key) => fields[key] && fields[key]!.show !== false);

  function setField(key: AddressFieldKey, text: string) {
    const next: AddressValue = { ...current };
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
              type="text"
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
