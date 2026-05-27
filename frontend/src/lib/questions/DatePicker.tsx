// ============================================================================
// OpenSurvey — date question (native date picker).
// Answer shape: 'YYYY-MM-DD' string (survey-schema.json #/$defs/answerShapes).
//
// We use a native <input type="date">, which always reads/writes the ISO
// `YYYY-MM-DD` value (exactly the stored answer shape) and renders the control
// per the user's locale. config.format (DD-MM-YYYY / MM-DD-YYYY) is a DISPLAY
// preference the native control cannot honor; it is intentionally advisory here
// (see the FOLLOWUPS note in the report). config.min/max gate the selectable
// range at the input level; answers.ts re-checks them for completeness.
// ============================================================================
import type { DateConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function DatePicker({
  config,
  value,
  onChange,
  inputId,
}: QuestionRendererProps<DateConfig, string>) {
  const current = typeof value === "string" ? value : "";

  return (
    <input
      type="date"
      id={inputId}
      value={current}
      min={config.min || undefined}
      max={config.max || undefined}
      onChange={(e) => {
        const v = e.target.value;
        onChange(v === "" ? undefined : v);
      }}
      className={
        "w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900 " +
        "shadow-sm transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
      }
    />
  );
}
