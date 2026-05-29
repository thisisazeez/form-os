/**
 * TypeScript runner for the shared TS<->PY logic-fixture suite (vitest).
 *
 * Loads EVERY case in `fixtures/*.json` (the SAME corpus the Python runner uses)
 * and asserts the (injectable) TS evaluator's behavior matches the
 * language-neutral expectations. CI runs both runners so parity is enforced
 * (REVIEW.md §6).
 *
 * The evaluator is resolved through `./evaluator-under-test` (a one-line shim) so
 * the real Wave-3 evaluator can be dropped in without touching this file.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { nextStep, requiredQuestionIds, validateSurvey, type NextStep } from "./evaluator-under-test";
import type { AnswerMap } from "../../frontend/src/lib/types";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = join(HERE, "fixtures");

interface FixtureCase {
  name: string;
  description?: string;
  survey: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  answers?: AnswerMap;
  currentQuestionId?: string | null;
  expected_next?: NextStep;
  expected_required?: string[];
  expectValid?: boolean;
  expectedErrorCode?: string;
  _file?: string;
}

function loadCases(): FixtureCase[] {
  const files = readdirSync(FIXTURES_DIR)
    .filter((f) => f.endsWith(".json"))
    .sort();
  if (files.length === 0) {
    throw new Error(`no fixture files found in ${FIXTURES_DIR}`);
  }
  const cases: FixtureCase[] = [];
  const seen = new Set<string>();
  for (const file of files) {
    const payload = JSON.parse(readFileSync(join(FIXTURES_DIR, file), "utf-8"));
    const items: FixtureCase[] = Array.isArray(payload) ? payload : [payload];
    for (const c of items) {
      if (seen.has(c.name)) {
        throw new Error(`duplicate fixture name across corpus: ${c.name}`);
      }
      seen.add(c.name);
      c._file = file;
      cases.push(c);
    }
  }
  return cases;
}

/** Normalize a NextStep so an absent `id` and `id: undefined/null` compare equal (README §2). */
function normalizeNext(step: NextStep): { type: string; id?: string } {
  const anyStep = step as { type: string; id?: string };
  const out: { type: string; id?: string } = { type: anyStep.type };
  if (anyStep.id !== undefined && anyStep.id !== null) out.id = anyStep.id;
  return out;
}

const CASES = loadCases();

describe("shared logic fixtures (TS)", () => {
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

      if (c.expectValid === true && c.expected_next === undefined) {
        return; // pure positive-validation case
      }

      // nextStep assertion
      const actual = nextStep(c.survey, c.answers ?? {}, c.currentQuestionId ?? null);
      expect(normalizeNext(actual), `next_step mismatch for ${c.name}`).toEqual(
        normalizeNext(c.expected_next as NextStep),
      );

      // optional required-set assertion
      if (c.expected_required !== undefined) {
        const actualRequired = requiredQuestionIds(c.survey, c.answers ?? {});
        expect([...actualRequired].sort(), `required mismatch for ${c.name}`).toEqual(
          [...c.expected_required].sort(),
        );
      }
    });
  }

  it("corpus is non-trivial", () => {
    expect(CASES.length).toBeGreaterThanOrEqual(20);
  });
});
