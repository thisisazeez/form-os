// ============================================================================
// onForm — single select question (radio list or dropdown).
//
// Answer shape (survey-schema.json #/$defs/answerShapes):
//   - choiceId (string) for a normal choice
//   - {choiceId, other: "<typed text>"} when the selected choice has isOther:true
// ============================================================================
import { resolveI18n } from "../i18n";
import type { OtherChoiceAnswer, SingleSelectConfig } from "../types";
import type { QuestionRendererProps } from "./types";

type Value = string | OtherChoiceAnswer;

function selectedId(value: Value | undefined): string | undefined {
  if (value === undefined) return undefined;
  return typeof value === "string" ? value : value.choiceId;
}

function otherText(value: Value | undefined): string {
  return value !== undefined && typeof value === "object" ? value.other : "";
}

export function SingleSelect({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<SingleSelectConfig, Value>) {
  const chosen = selectedId(value);
  const choices = config.choices ?? [];

  function pick(choiceId: string, isOther: boolean | undefined) {
    if (isOther) {
      // Preserve any already-typed "other" text when re-selecting the same choice.
      const existing = selectedId(value) === choiceId ? otherText(value) : "";
      onChange({ choiceId, other: existing });
    } else {
      onChange(choiceId);
    }
  }

  if (config.display === "dropdown") {
    const otherChoice = choices.find((c) => c.isOther && c.id === chosen);
    return (
      <div className="space-y-3">
        <select
          id={inputId}
          value={chosen ?? ""}
          onChange={(e) => {
            const id = e.target.value;
            if (id === "") {
              onChange(undefined);
              return;
            }
            const c = choices.find((x) => x.id === id);
            pick(id, c?.isOther);
          }}
          className="w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
        >
          <option value="">—</option>
          {choices.map((c) => (
            <option key={c.id} value={c.id}>
              {resolveI18n(c.label, lang, defaultLang)}
            </option>
          ))}
        </select>
        {otherChoice && (
          <input
            type="text"
            aria-label={resolveI18n(otherChoice.label, lang, defaultLang)}
            value={otherText(value)}
            onChange={(e) => onChange({ choiceId: otherChoice.id, other: e.target.value })}
            placeholder="Please specify"
            className="w-full rounded-lg border border-slate-300 px-4 py-2 text-base focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
          />
        )}
      </div>
    );
  }

  // list display (radios)
  return (
    <div role="radiogroup" aria-labelledby={`${inputId}-label`} className="space-y-2">
      {choices.map((c, i) => {
        const isChosen = chosen === c.id;
        return (
          <div key={c.id}>
            <label
              className={
                "flex cursor-pointer items-center gap-3 rounded-lg border px-4 py-3 text-base transition " +
                (isChosen
                  ? "border-indigo-500 bg-indigo-50 text-indigo-900"
                  : "border-slate-300 bg-white text-slate-900 hover:border-slate-400")
              }
            >
              <input
                type="radio"
                name={inputId}
                id={i === 0 ? inputId : `${inputId}-${c.id}`}
                value={c.id}
                checked={isChosen}
                onChange={() => pick(c.id, c.isOther)}
                className="h-4 w-4 accent-indigo-600"
              />
              <span>{resolveI18n(c.label, lang, defaultLang)}</span>
            </label>
            {c.isOther && isChosen && (
              <input
                type="text"
                aria-label={`${resolveI18n(c.label, lang, defaultLang)} — specify`}
                value={otherText(value)}
                onChange={(e) => onChange({ choiceId: c.id, other: e.target.value })}
                placeholder="Please specify"
                className="mt-2 w-full rounded-lg border border-slate-300 px-4 py-2 text-base focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
