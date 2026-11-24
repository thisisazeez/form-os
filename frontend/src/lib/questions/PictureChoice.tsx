// ============================================================================
// onForm — picture-choice question (image-based choices).
//
// Answer shape (survey-schema.json #/$defs/answerShapes):
//   - allowMulti: false  -> choiceId (string) of the selected image
//   - allowMulti: true   -> [choiceId] (array of selected image ids)
//
// Renders a selectable grid of images (each option carries an imageUrl + an
// optional label). Single mode behaves like a radio group (click toggles off);
// multi mode toggles each tile independently. When the captured answer's shape
// doesn't match the current allowMulti (e.g. the author flipped the toggle after
// a response was seeded), we ignore the mismatched value rather than rendering a
// hybrid selection — the answer is re-captured cleanly on the next click.
// ============================================================================
import { resolveI18n } from "../i18n";
import type { PictureChoiceConfig } from "../types";
import type { QuestionRendererProps } from "./types";

type Value = string | string[];

export function PictureChoice({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<PictureChoiceConfig, Value>) {
  const options = config.options ?? [];
  const multi = config.allowMulti === true;

  // Read the current selection, honoring the active mode. A shape mismatch
  // (string while multi, or array while single) reads as "nothing selected".
  const selected: string[] = multi
    ? Array.isArray(value)
      ? value
      : []
    : typeof value === "string"
      ? [value]
      : [];

  function isOn(id: string): boolean {
    return selected.includes(id);
  }

  function pickSingle(id: string) {
    // Standard radio semantics: selecting an image sets it. (Re-selecting the
    // same radio does not fire a change event, so there is no toggle-off here —
    // matching native single-choice behavior; an optional question is cleared by
    // not selecting at all.)
    onChange(id);
  }

  function toggleMulti(id: string) {
    const base = Array.isArray(value) ? value : [];
    const next = base.includes(id) ? base.filter((x) => x !== id) : [...base, id];
    onChange(next.length === 0 ? undefined : next);
  }

  return (
    <div
      role={multi ? "group" : "radiogroup"}
      aria-labelledby={`${inputId}-label`}
      className="grid grid-cols-2 gap-3 sm:grid-cols-3"
    >
      {options.map((opt, i) => {
        const on = isOn(opt.id);
        const label = resolveI18n(opt.label, lang, defaultLang);
        const a11yLabel = label || `Option ${i + 1}`;
        return (
          <label
            key={opt.id}
            className={
              "group relative flex cursor-pointer flex-col overflow-hidden rounded-lg border-2 transition " +
              (on
                ? "border-indigo-500 ring-2 ring-indigo-200"
                : "border-slate-200 hover:border-slate-300")
            }
          >
            <input
              type={multi ? "checkbox" : "radio"}
              name={multi ? undefined : inputId}
              id={i === 0 ? inputId : `${inputId}-${opt.id}`}
              checked={on}
              aria-label={a11yLabel}
              onChange={() => (multi ? toggleMulti(opt.id) : pickSingle(opt.id))}
              className="sr-only"
            />
            <span className="block aspect-square w-full overflow-hidden bg-slate-100">
              {/* eslint-disable-next-line jsx-a11y/img-redundant-alt */}
              <img
                src={opt.imageUrl}
                alt={a11yLabel}
                loading="lazy"
                className="h-full w-full object-cover"
              />
            </span>
            {label && (
              <span className="block px-2 py-1.5 text-center text-sm text-slate-800">{label}</span>
            )}
            {on && (
              <span
                aria-hidden="true"
                className="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-indigo-600 text-xs font-bold text-white"
              >
                ✓
              </span>
            )}
          </label>
        );
      })}
    </div>
  );
}
