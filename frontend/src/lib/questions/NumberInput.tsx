// ============================================================================
// OpenSurvey — number question (numeric input).
// Answer shape: number (survey-schema.json #/$defs/answerShapes).
//
// The answer is a real number (not a string): an empty field clears to
// undefined, otherwise we emit Number(value). min/max/step constrain the native
// control; answers.ts re-checks min/max for completeness (0 is a valid answer,
// never treated as empty — see isEmptyAnswer).
// ============================================================================
import { resolveI18n } from "../i18n";
import type { NumberConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function NumberInput({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<NumberConfig, number>) {
  const placeholder = resolveI18n(config.placeholder, lang, defaultLang);
  const current = typeof value === "number" ? String(value) : "";

  return (
    <input
      type="number"
      inputMode="decimal"
      id={inputId}
      value={current}
      min={config.min}
      max={config.max}
      step={config.step}
      placeholder={placeholder || undefined}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") {
          onChange(undefined);
          return;
        }
        const n = Number(raw);
        onChange(Number.isNaN(n) ? undefined : n);
      }}
      className={
        "w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900 " +
        "shadow-sm transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
      }
    />
  );
}
