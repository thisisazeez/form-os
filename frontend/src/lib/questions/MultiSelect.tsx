// ============================================================================
// onForm — multi select question (checkbox list).
// Answer shape: [choiceId] (array of selected choice ids).
//
// minSelections/maxSelections are enforced at the answer-gating layer (the
// runtime's "can advance" check); here we only cap NEW selections at
// maxSelections so the user can't exceed it.
// ============================================================================
import { resolveI18n } from "../i18n";
import type { MultiSelectConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function MultiSelect({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<MultiSelectConfig, string[]>) {
  const selected = Array.isArray(value) ? value : [];
  const choices = config.choices ?? [];
  const max = config.maxSelections;

  function toggle(choiceId: string) {
    const isOn = selected.includes(choiceId);
    let next: string[];
    if (isOn) {
      next = selected.filter((id) => id !== choiceId);
    } else {
      if (max !== undefined && selected.length >= max) return; // at cap; ignore
      next = [...selected, choiceId];
    }
    onChange(next.length === 0 ? undefined : next);
  }

  return (
    <div role="group" aria-labelledby={`${inputId}-label`} className="space-y-2">
      {choices.map((c, i) => {
        const isOn = selected.includes(c.id);
        const atCap = !isOn && max !== undefined && selected.length >= max;
        return (
          <label
            key={c.id}
            className={
              "flex items-center gap-3 rounded-lg border px-4 py-3 text-base transition " +
              (isOn
                ? "border-indigo-500 bg-indigo-50 text-indigo-900"
                : atCap
                  ? "cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400"
                  : "cursor-pointer border-slate-300 bg-white text-slate-900 hover:border-slate-400")
            }
          >
            <input
              type="checkbox"
              id={i === 0 ? inputId : `${inputId}-${c.id}`}
              checked={isOn}
              disabled={atCap}
              onChange={() => toggle(c.id)}
              className="h-4 w-4 accent-indigo-600"
            />
            <span>{resolveI18n(c.label, lang, defaultLang)}</span>
          </label>
        );
      })}
    </div>
  );
}
