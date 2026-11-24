// ============================================================================
// onForm — Builder BLOCKS editor (Blocks tab).
//
// Question-grouping blocks (survey-schema.json #/$defs/block) are an ADDITIVE,
// OPTIONAL overlay: the flat `questions[]` array stays the single source of truth
// for flow + logic. This panel lets the author GROUP questions into named blocks,
// set a per-block WITHIN-BLOCK randomization mode, REORDER blocks (metadata only —
// never touches questions[]), add/remove member questions, and UNGROUP a block.
//
// All edits go through the pure surveyModel block transforms, which return a FRESH
// Survey (or the SAME survey on a no-op). BuilderApp's onChange drives dirty-tracking
// + Save (toSurveyWrite forwards survey.blocks under the `blocks` body key).
//
// Our OWN UI (not a copy of the target's): a stacked list of block cards, each with a
// title input, a randomize <select>, member chips (removable), and an "add question"
// dropdown listing the still-ungrouped questions. A small "Ungrouped" footer shows the
// questions not in any block (informational). DEFERRED: block-level logic (out of scope).
// ============================================================================
import { resolveI18n } from "../../lib/i18n";
import type { Block, BlockRandomize, Survey } from "../../lib/types";
import {
  addQuestionToBlock,
  createBlock,
  moveBlockDown,
  moveBlockUp,
  removeQuestionFromBlock,
  setBlockRandomize,
  setBlockTitle,
  ungroupBlock,
  ungroupedQuestionIds,
} from "./surveyModel";

export interface BlocksPanelProps {
  survey: Survey;
  onChange: (next: Survey) => void;
  /** Authoring language for resolving question headlines in chips/menus. */
  lang?: string;
  defaultLang?: string;
}

const RANDOMIZE_LABELS: Record<BlockRandomize, string> = {
  none: "Keep order",
  all: "Shuffle all",
  exceptLast: "Shuffle (pin last)",
};

export function BlocksPanel({ survey, onChange, lang = "default", defaultLang = "default" }: BlocksPanelProps) {
  const blocks: Block[] = survey.blocks ?? [];

  // Resolve a question's display headline for chips / the add-question menu.
  function headlineOf(questionId: string): string {
    const q = survey.questions.find((x) => x.id === questionId);
    if (!q) return questionId;
    const text = resolveI18n(q.headline, lang, defaultLang);
    return text?.trim() ? text : questionId;
  }

  const ungroupedIds = ungroupedQuestionIds(survey);

  return (
    <section data-testid="blocks-panel" className="space-y-4">
      <header>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Question blocks</h3>
        <p className="mt-1 text-xs text-muted/80">
          Group questions into blocks and optionally randomize the order they’re shown within a
          block. Blocks are a display grouping only — they don’t change your questions’ saved order
          or logic.
        </p>
      </header>

      {/* Block cards */}
      {blocks.length === 0 ? (
        <p className="rounded-lg border border-dashed border-hairline px-3 py-4 text-center text-xs text-muted/70">
          No blocks yet. Create a block to group questions.
        </p>
      ) : (
        <ul className="space-y-3">
          {blocks.map((block, index) => (
            <li
              key={block.id}
              data-testid="block-card"
              className="rounded-xl border border-hairline bg-white p-3 shadow-sm"
            >
              <div className="flex items-center gap-2">
                <span className="shrink-0 rounded-md bg-panel px-2 py-0.5 text-[11px] font-semibold text-muted">
                  Block {index + 1}
                </span>
                <input
                  type="text"
                  value={block.title ?? ""}
                  onChange={(e) => onChange(setBlockTitle(survey, block.id, e.target.value))}
                  placeholder={`Block ${index + 1}`}
                  aria-label={`Block ${index + 1} title`}
                  className="min-w-0 flex-1 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                />
                {/* Reorder controls (metadata-only — never reorders questions[]) */}
                <button
                  type="button"
                  onClick={() => onChange(moveBlockUp(survey, index))}
                  disabled={index === 0}
                  aria-label={`Move block ${index + 1} up`}
                  title="Move up"
                  className="rounded p-1 text-muted hover:bg-panel hover:text-ink disabled:opacity-30"
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => onChange(moveBlockDown(survey, index))}
                  disabled={index === blocks.length - 1}
                  aria-label={`Move block ${index + 1} down`}
                  title="Move down"
                  className="rounded p-1 text-muted hover:bg-panel hover:text-ink disabled:opacity-30"
                >
                  ↓
                </button>
                <button
                  type="button"
                  onClick={() => onChange(ungroupBlock(survey, block.id))}
                  aria-label={`Ungroup block ${index + 1}`}
                  title="Ungroup (delete block, keep questions)"
                  className="rounded p-1 text-muted hover:bg-rose-50 hover:text-rose-600"
                >
                  ✕
                </button>
              </div>

              {/* Randomize mode */}
              <label className="mt-2.5 flex items-center gap-2 text-xs font-medium text-muted">
                Order
                <select
                  value={block.randomize ?? "none"}
                  onChange={(e) =>
                    onChange(setBlockRandomize(survey, block.id, e.target.value as BlockRandomize))
                  }
                  aria-label={`Block ${index + 1} randomize`}
                  className="rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                >
                  {(["none", "all", "exceptLast"] as BlockRandomize[]).map((mode) => (
                    <option key={mode} value={mode}>
                      {RANDOMIZE_LABELS[mode]}
                    </option>
                  ))}
                </select>
              </label>

              {/* Member question chips */}
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {block.questionIds.length === 0 ? (
                  <span className="text-xs text-muted/70">No questions in this block.</span>
                ) : (
                  block.questionIds.map((qid) => (
                    <span
                      key={qid}
                      className="inline-flex items-center gap-1.5 rounded-full bg-panel py-0.5 pl-2.5 pr-1.5 text-xs font-medium text-muted"
                    >
                      <span className="max-w-[14rem] truncate text-ink">{headlineOf(qid)}</span>
                      <button
                        type="button"
                        onClick={() => onChange(removeQuestionFromBlock(survey, block.id, qid))}
                        aria-label={`Remove ${qid} from block ${index + 1}`}
                        title="Remove from block"
                        className="rounded p-0.5 text-muted hover:bg-rose-50 hover:text-rose-600"
                      >
                        ✕
                      </button>
                    </span>
                  ))
                )}
              </div>

              {/* Add a question (the still-ungrouped ones) */}
              {ungroupedIds.length > 0 && (
                <label className="mt-2 flex items-center gap-2 text-xs font-medium text-muted">
                  Add question
                  <select
                    value=""
                    onChange={(e) => {
                      if (e.target.value) {
                        onChange(addQuestionToBlock(survey, block.id, e.target.value));
                      }
                    }}
                    aria-label={`Add a question to block ${index + 1}`}
                    className="min-w-0 max-w-[18rem] flex-1 rounded-lg border border-dashed border-hairline bg-white px-2.5 py-1.5 text-sm text-brand shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                  >
                    <option value="">+ Add a question…</option>
                    {ungroupedIds.map((qid) => (
                      <option key={qid} value={qid}>
                        {headlineOf(qid)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Create-block button */}
      <button
        type="button"
        onClick={() => onChange(createBlock(survey, `Block ${blocks.length + 1}`))}
        className="rounded-lg border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand transition-colors hover:border-brand hover:bg-brand/5"
      >
        + Create block
      </button>

      {/* Ungrouped questions footer (informational) */}
      {survey.questions.length > 0 && (
        <div className="border-t border-hairline pt-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted/80">
            Ungrouped questions
          </h4>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {ungroupedIds.length === 0 ? (
              <span className="text-xs text-muted/70">All questions are in a block.</span>
            ) : (
              ungroupedIds.map((qid) => (
                <span
                  key={qid}
                  className="inline-flex items-center rounded-full bg-panel px-2.5 py-0.5 text-xs font-medium text-muted"
                >
                  <span className="max-w-[14rem] truncate">{headlineOf(qid)}</span>
                </span>
              ))
            )}
          </div>
        </div>
      )}
    </section>
  );
}
