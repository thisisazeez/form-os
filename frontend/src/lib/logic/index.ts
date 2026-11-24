// ============================================================================
// onForm — Logic engine public barrel.
//
// The runtime island, the builder (Wave 3), and the shared TS<->PY fixture shim
// (tests/logic_fixtures/evaluator-under-test.ts) import from here. MUST export
// the three frozen contract functions + the NextStep type (README §6/§7):
//   nextStep, requiredQuestionIds, validateSurvey.
// ============================================================================
export { computeVariables, nextStep, requiredQuestionIds, validateSurvey } from "./evaluator";
export type { NextStep, ValidationError, ValidationResult } from "./evaluator";
