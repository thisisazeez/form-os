// ============================================================================
// onForm — NPS question (fixed 0..10 scale, 11 buttons).
// Answer shape: integer 0..10. (No range config — survey-schema.json q_nps.)
// ============================================================================
import { resolveI18n } from "../i18n";
import type { NpsConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function Nps({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<NpsConfig, number>) {
  const current = typeof value === "number" ? value : null;
  const lower = resolveI18n(config?.lowerLabel, lang, defaultLang);
  const upper = resolveI18n(config?.upperLabel, lang, defaultLang);
  const steps = Array.from({ length: 11 }, (_, i) => i); // 0..10

  return (
    <div className="space-y-2">
      <div role="radiogroup" aria-labelledby={`${inputId}-label`} className="flex flex-wrap gap-1.5">
        {steps.map((n) => {
          const active = current === n;
          return (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={`${n}`}
              id={n === 0 ? inputId : undefined}
              onClick={() => onChange(active ? undefined : n)}
              className={
                "flex h-11 w-11 items-center justify-center rounded-lg border text-base font-medium transition " +
                (active
                  ? "border-indigo-500 bg-indigo-600 text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:border-indigo-400")
              }
            >
              {n}
            </button>
          );
        })}
      </div>
      {(lower || upper) && (
        <div className="flex justify-between text-xs text-slate-500">
          <span>{lower}</span>
          <span>{upper}</span>
        </div>
      )}
    </div>
  );
}
