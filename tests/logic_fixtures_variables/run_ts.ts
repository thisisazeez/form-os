/**
 * TypeScript runner for the VARIABLES TS<->PY parity suite (feat/parity-variables).
 *
 * Runs the SAME fixtures as run_py.py against the PRODUCTION TS evaluators (the runtime + builder
 * use these) to prove byte-identical TS<->PY results on the NEW survey-variables + `calculate`
 * behavior. SEPARATE from tests/logic_fixtures (the frozen 64-case corpus stays untouched).
 *
 *   computeVariables / nextStep / requiredQuestionIds / validateSurvey  (frontend/src/lib/logic)
 *   resolveRecall                                                       (frontend/src/lib/recall)
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  computeVariables,
  nextStep,
  requiredQuestionIds,
  validateSurvey,
  type NextStep,
} from "../../frontend/src/lib/logic";
import { resolveRecall } from "../../frontend/src/lib/recall";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = join(HERE, "fixtures");

interface FixtureCase {
  name: string;
  description?: string;
  survey: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  answers?: Record<string, unknown>;
  currentQuestionId?: string | null;
  expected_next?: NextStep;
  expected_required?: string[];
  expected_variables?: Record<string, unknown>;
  recall_text?: string;
  expected_recall?: string;
  expectValid?: boolean;
  expectedErrorCode?: string;
  _file?: string;
}

function loadCases(): FixtureCase[] {
  const files = readdirSync(FIXTURES_DIR)
    .filter((f) => f.endsWith(".json"))
    .sort();
  if (files.length === 0) throw new Error(`no fixture files found in ${FIXTURES_DIR}`);
  const cases: FixtureCase[] = [];
  const seen = new Set<string>();
  for (const file of files) {
    const payload = JSON.parse(readFileSync(join(FIXTURES_DIR, file), "utf-8"));
    const items: FixtureCase[] = Array.isArray(payload) ? payload : [payload];
    for (const c of items) {
      if (seen.has(c.name)) throw new Error(`duplicate fixture name across corpus: ${c.name}`);
      seen.add(c.name);
      c._file = file;
      cases.push(c);
    }
  }
  return cases;
}

function normalizeNext(step: NextStep): { type: string; id?: string } {
  const anyStep = step as { type: string; id?: string };
  const out: { type: string; id?: string } = { type: anyStep.type };
  if (anyStep.id !== undefined && anyStep.id !== null) out.id = anyStep.id;
  return out;
}

/** Numbers may be int (JSON literal) or float (arithmetic); compare as Number so 15 == 15.0. */
function normalizeVars(vars: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(vars)) {
    out[k] = typeof v === "number" ? Number(v) : v;
  }
  return out;
}

const CASES = loadCases();

describe("survey-variables + calculate fixtures (TS)", () => {
  for (const c of CASES) {
    it(c.name, () => {
      // Validation-only case
      if (c.expectValid === false) {
        const result = validateSurvey(c.survey);
        expect(result.ok, `expected validateSurvey to FAIL for ${c.name}`).toBe(false);
        if (c.expectedErrorCode !== undefined && result.ok === false) {
          const codes = result.errors.map((e) => e.code);
          expect(codes, `expected error code ${c.expectedErrorCode} in ${JSON.stringify(codes)}`).toContain(
            c.expectedErrorCode,
          );
        }
        return;
      }

      // Positive cases: validateSurvey should pass.
      const vresult = validateSurvey(c.survey);
      expect(vresult.ok, `validateSurvey unexpectedly failed for ${c.name}`).toBe(true);

      const answers = c.answers ?? {};

      // computeVariables assertion
      const variables = computeVariables(c.survey, answers);
      if (c.expected_variables !== undefined) {
        expect(normalizeVars(variables), `computeVariables mismatch for ${c.name}`).toEqual(
          normalizeVars(c.expected_variables),
        );
      }

      // nextStep assertion (optional)
      if (c.expected_next !== undefined) {
        const actual = nextStep(c.survey, answers, c.currentQuestionId ?? null);
        expect(normalizeNext(actual), `nextStep mismatch for ${c.name}`).toEqual(
          normalizeNext(c.expected_next),
        );
      }

      // required-set assertion (optional)
      if (c.expected_required !== undefined) {
        const actualRequired = requiredQuestionIds(c.survey, answers);
        expect([...actualRequired].sort(), `required mismatch for ${c.name}`).toEqual(
          [...c.expected_required].sort(),
        );
      }

      // recall-of-variable assertion (optional)
      if (c.expected_recall !== undefined) {
        const recallAnswers = { ...answers, ...variables };
        const rendered = resolveRecall(c.recall_text, recallAnswers as Record<string, never>);
        expect(rendered, `recall mismatch for ${c.name}`).toBe(c.expected_recall);
      }
    });
  }

  it("corpus is non-trivial", () => {
    expect(CASES.length).toBeGreaterThanOrEqual(15);
  });
});
