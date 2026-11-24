// ============================================================================
// onForm — Shared props for question-renderer components.
// ============================================================================
import type { AnswerValue, Question } from "../types";

/**
 * Every question renderer takes this uniform prop shape. The renderer reads the
 * active language for display, the current answer value (or undefined), and
 * reports changes via `onChange` with the answer shape from
 * survey-schema.json #/$defs/answerShapes.
 */
export interface QuestionRendererProps<TConfig = unknown, TValue = AnswerValue> {
  question: Question;
  config: TConfig;
  /** Current answer value for this question (undefined if unanswered). */
  value: TValue | undefined;
  /** Report a new answer value (or undefined to clear). */
  onChange: (value: TValue | undefined) => void;
  /** Active response language code (e.g. "en"). */
  lang: string;
  /** Survey default-language code (fallback for i18n resolution). */
  defaultLang: string;
  /** Stable DOM id for the primary input (label association / focus). */
  inputId: string;
}
