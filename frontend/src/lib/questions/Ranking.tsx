// ============================================================================
// onForm — ranking question (order a list of options).
// Answer shape: [choiceId] ordered (survey-schema.json #/$defs/answerShapes).
//
// The answer is the FULL ordered list of choice ids. A ranking always HAS an
// order (the displayed one), so we seed the answer to the config order on mount
// — a respondent who agrees with the presented order can advance immediately
// (a required ranking is satisfied by the seeded order, matching common survey
// tools). The respondent then reorders with accessible up/down controls (no
// drag-drop dependency, so this stays testable and matches the dependency-free
// runtime).
// ============================================================================
import { useEffect } from "react";

import { resolveI18n } from "../i18n";
import type { RankingConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function Ranking({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<RankingConfig, string[]>) {
  const choices = config.choices ?? [];
  const labelFor = (id: string): string => {
    const c = choices.find((x) => x.id === id);
    return c ? resolveI18n(c.label, lang, defaultLang) : id;
  };

  // The displayed order: the captured answer if present (filtered to live ids,
  // with any newly-added choices appended), else the config order.
  const configIds = choices.map((c) => c.id);
  const captured = Array.isArray(value) ? value.filter((id) => configIds.includes(id)) : [];
  const order = captured.length > 0
    ? [...captured, ...configIds.filter((id) => !captured.includes(id))]
    : configIds;

  // Seed the answer to the displayed order on mount (when nothing is captured yet)
  // so a respondent who accepts the presented order can advance without a no-op
  // move. Only fires while there's a real ordering to record.
  useEffect(() => {
    if (captured.length === 0 && configIds.length > 0) {
      onChange(configIds);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function move(index: number, dir: -1 | 1) {
    const target = index + dir;
    if (target < 0 || target >= order.length) return;
    const next = [...order];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  return (
    <ol aria-labelledby={`${inputId}-label`} className="space-y-2">
      {order.map((id, i) => (
        <li
          key={id}
          id={i === 0 ? inputId : undefined}
          className="flex items-center gap-3 rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900"
        >
          <span
            aria-hidden="true"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-sm font-semibold text-indigo-700"
          >
            {i + 1}
          </span>
          <span className="min-w-0 flex-1 truncate">{labelFor(id)}</span>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => move(i, -1)}
              disabled={i === 0}
              aria-label={`Move ${labelFor(id)} up`}
              className="rounded p-1 text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 disabled:opacity-30"
            >
              ▲
            </button>
            <button
              type="button"
              onClick={() => move(i, 1)}
              disabled={i === order.length - 1}
              aria-label={`Move ${labelFor(id)} down`}
              className="rounded p-1 text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 disabled:opacity-30"
            >
              ▼
            </button>
          </div>
        </li>
      ))}
    </ol>
  );
}
