// ============================================================================
// OpenSurvey — rating question (stars / numbers / smileys, range 3..10).
// Answer shape: integer 1..range.
// ============================================================================
import { resolveI18n } from "../i18n";
import type { RatingConfig } from "../types";
import type { QuestionRendererProps } from "./types";

const SMILEYS = ["😞", "🙁", "😐", "🙂", "😀", "😍", "🤩", "🥳", "💯", "🏆"];

export function Rating({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<RatingConfig, number>) {
  const range = config.range ?? 5;
  const steps = Array.from({ length: range }, (_, i) => i + 1);
  const current = typeof value === "number" ? value : 0;
  const lower = resolveI18n(config.lowerLabel, lang, defaultLang);
  const upper = resolveI18n(config.upperLabel, lang, defaultLang);

  function symbol(step: number, active: boolean): string {
    if (config.scale === "star") return active ? "★" : "☆";
    if (config.scale === "smiley") return SMILEYS[step - 1] ?? "🙂";
    return String(step); // number scale
  }

  return (
    <div className="space-y-2">
      <div role="radiogroup" aria-labelledby={`${inputId}-label`} className="flex flex-wrap gap-2">
        {steps.map((step) => {
          const active = config.scale === "star" ? step <= current : step === current;
          const isStar = config.scale === "star";
          return (
            <button
              key={step}
              type="button"
              role="radio"
              aria-checked={step === current}
              aria-label={`${step}`}
              id={step === 1 ? inputId : undefined}
              onClick={() => onChange(step === current ? undefined : step)}
              className={
                isStar
                  ? "text-3xl leading-none transition " +
                    (active ? "text-amber-400" : "text-slate-300 hover:text-amber-300")
                  : "flex h-11 min-w-11 items-center justify-center rounded-lg border px-3 text-lg transition " +
                    (active
                      ? "border-indigo-500 bg-indigo-600 text-white"
                      : "border-slate-300 bg-white text-slate-700 hover:border-indigo-400")
              }
            >
              {symbol(step, active)}
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
