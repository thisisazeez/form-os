// ============================================================================
// onForm — shared frontend library barrel.
//
// Public surface shared by the runtime island (and, later, the builder island):
//   - the logic engine (nextStep / requiredQuestionIds / validateSurvey)
//   - survey + answer TS types
//   - i18n + answer-gating helpers
//   - the question-renderer components
// ============================================================================
export { BRAND } from "./brand"; // generated from brand/tokens.json
export type { BrandColor } from "./brand";
export * from "./types"; // includes NextStep
export { computeVariables, nextStep, requiredQuestionIds, validateSurvey } from "./logic";
export type { ValidationError, ValidationResult } from "./logic";
export { resolveI18n, hasI18n, defaultLanguageCode, resolveActiveLanguage } from "./i18n";
export { resolveRecall, hasRecallToken, RECALL_TOKEN_RE } from "./recall";
export { sanitizeHtml, hasRichText, ALLOWED_TAGS, ALLOWED_ATTR } from "./sanitizeHtml";
export { isEmptyAnswer, validateAnswer } from "./answers";
export { applyBlockRandomization, presentBlockOrder } from "./blocks";
export { QuestionRenderer, isSupported, SUPPORTED_TYPES } from "./questions/QuestionRenderer";
export {
  BRAND_DEFAULTS,
  questionTextStyle,
  cardStyle,
  primaryButtonStyle,
  ctaButtonStyle,
  progressBarColor,
  pageBackgroundStyle,
  cardArrangementStyle,
} from "./styling";
