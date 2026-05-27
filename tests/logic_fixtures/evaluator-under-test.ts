/**
 * Indirection shim: the single place the TS runner resolves the evaluator from.
 *
 * Today it re-exports the REFERENCE evaluator so the shared fixture corpus is
 * green before Wave 3 exists. When Wave 3 ships the real TS evaluator (e.g.
 * `@opensurvey/runtime/logic`), repoint this re-export at that module — the
 * fixtures and the runner do NOT change (README §6, "Injecting the real
 * evaluator").
 *
 * The target module MUST export: nextStep, requiredQuestionIds, validateSurvey
 * (README §7).
 */
// Wave 3: repointed at the REAL production TS engine (frontend/src/lib/logic).
// The runner (run_ts.ts) and the fixtures are untouched — only this shim moves
// (README §6, "Injecting the real evaluator"). The target module exports the
// three frozen contract functions + the NextStep type (README §7).
export { nextStep, requiredQuestionIds, validateSurvey } from "../../frontend/src/lib/logic";
export type { NextStep } from "../../frontend/src/lib/logic";
