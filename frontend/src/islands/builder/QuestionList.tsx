// ============================================================================
// onForm — Builder question list (select + reorder + delete).
//
// Shows the ordered questions. Reordering uses up/down arrow buttons (keyboard-
// accessible, zero new deps — drag-drop is deferred). Selecting a row opens it in
// the editor panel. The list order IS the survey's linear flow (logic overrides
// via jumpTo, authored in the deferred logic editor).
// ============================================================================
import { resolveI18n } from "../../lib/i18n";
import { defaultLanguageCode } from "../../lib/i18n";
import type { Question, Survey } from "../../lib/types";

const TYPE_LABEL: Record<string, string> = {
  openText: "Text",
  singleSelect: "Single select",
  multiSelect: "Multi select",
  rating: "Rating",
  nps: "NPS",
  cta: "Statement / CTA",
  consent: "Consent",
};

export function QuestionList({
  survey,
  selectedId,
  onSelect,
  onMoveUp,
  onMoveDown,
  onDelete,
}: {
  survey: Survey;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onMoveUp: (index: number) => void;
  onMoveDown: (index: number) => void;
  onDelete: (id: string) => void;
}) {
  const defaultLang = defaultLanguageCode(survey.languages);

  if (survey.questions.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-hairline bg-white px-4 py-8 text-center text-sm text-muted">
        No questions yet. Add one from the palette above.
      </div>
    );
  }

  return (
    <ol className="space-y-2">
      {survey.questions.map((q, i) => (
        <QuestionRow
          key={q.id}
          question={q}
          index={i}
          total={survey.questions.length}
          defaultLang={defaultLang}
          selected={q.id === selectedId}
          onSelect={() => onSelect(q.id)}
          onMoveUp={() => onMoveUp(i)}
          onMoveDown={() => onMoveDown(i)}
          onDelete={() => onDelete(q.id)}
        />
      ))}
    </ol>
  );
}

function QuestionRow({
  question,
  index,
  total,
  defaultLang,
  selected,
  onSelect,
  onMoveUp,
  onMoveDown,
  onDelete,
}: {
  question: Question;
  index: number;
  total: number;
  defaultLang: string;
  selected: boolean;
  onSelect: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onDelete: () => void;
}) {
  const headline = resolveI18n(question.headline, defaultLang, defaultLang) || "(untitled)";
  const typeLabel = TYPE_LABEL[question.type] ?? question.type;

  return (
    <li
      className={
        "flex items-center gap-2 rounded-lg border px-3 py-2 transition-colors " +
        (selected
          ? "border-brand bg-brand/5"
          : "border-hairline bg-white hover:border-muted/40")
      }
    >
      {/* Reorder controls */}
      <div className="flex flex-col">
        <button
          type="button"
          onClick={onMoveUp}
          disabled={index === 0}
          aria-label={`Move "${headline}" up`}
          className="leading-none text-muted hover:text-ink disabled:opacity-30"
        >
          ▲
        </button>
        <button
          type="button"
          onClick={onMoveDown}
          disabled={index === total - 1}
          aria-label={`Move "${headline}" down`}
          className="leading-none text-muted hover:text-ink disabled:opacity-30"
        >
          ▼
        </button>
      </div>

      {/* Label (click to edit) */}
      <button type="button" onClick={onSelect} className="min-w-0 flex-1 text-left">
        <span className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted">{index + 1}</span>
          <span className="truncate text-sm font-medium text-ink">{headline}</span>
        </span>
        <span className="mt-0.5 flex items-center gap-2">
          <span className="rounded bg-panel px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">
            {typeLabel}
          </span>
          {question.required && (
            <span className="text-[10px] font-medium uppercase tracking-wide text-rose-500">
              required
            </span>
          )}
        </span>
      </button>

      {/* Delete */}
      <button
        type="button"
        onClick={onDelete}
        aria-label={`Delete "${headline}"`}
        className="rounded p-1 text-muted hover:bg-rose-50 hover:text-rose-600"
      >
        ✕
      </button>
    </li>
  );
}
