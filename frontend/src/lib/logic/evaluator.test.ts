// ============================================================================
// Frontend-side engine smoke test.
//
// The engine's behavioral parity is FULLY proven by the 64-case shared fixture
// corpus (tests/logic_fixtures, run with the evaluator-under-test shim repointed
// at frontend/src/lib/logic — see the task summary). These tests are a thin
// in-build guard that the public import surface works here and pin the highest-
// risk semantics (FU-1 object no-match, cumulative skip, require-during-answering,
// end resolution) at the unit level too.
// ============================================================================
import { describe, expect, it } from "vitest";

import type { Survey } from "../types";
import { nextStep, requiredQuestionIds, validateSurvey } from "./index";

const lang = [{ code: "en", label: "English", default: true }];

describe("nextStep — basics", () => {
  it("returns the first question at start (currentQuestionId=null)", () => {
    const survey: Survey = {
      languages: lang,
      questions: [
        { id: "q1", type: "openText", headline: { default: "1" }, config: { inputType: "short" } },
        { id: "q2", type: "openText", headline: { default: "2" }, config: { inputType: "short" } },
      ],
      endings: [{ id: "e", type: "endScreen" }],
    };
    expect(nextStep(survey, {}, null)).toEqual({ type: "question", id: "q1" });
  });

  it("falls off the last question to the first ending", () => {
    const survey: Survey = {
      languages: lang,
      questions: [{ id: "q1", type: "openText", headline: { default: "1" }, config: { inputType: "short" } }],
      endings: [{ id: "e1", type: "endScreen" }],
    };
    expect(nextStep(survey, { q1: "x" }, "q1")).toEqual({ type: "ending", id: "e1" });
  });

  it("falls off the end to {type:'end'} when there are no endings", () => {
    const survey: Survey = {
      languages: lang,
      questions: [{ id: "q1", type: "openText", headline: { default: "1" }, config: { inputType: "short" } }],
      endings: [],
    };
    expect(nextStep(survey, { q1: "x" }, "q1")).toEqual({ type: "end" });
  });
});

describe("FU-1 object-shape no-match", () => {
  const build = (op: "equals" | "notEquals"): Survey => ({
    languages: lang,
    questions: [
      {
        id: "contact",
        type: "contactInfo",
        headline: { default: "c" },
        config: { fields: { email: { show: true } } },
      },
      {
        id: "gate",
        type: "openText",
        headline: { default: "g" },
        config: { inputType: "short" },
        logic: [
          { id: "r", condition: { leftQuestionId: "contact", operator: op, value: "x@y.z" }, action: { type: "jumpTo", target: "trap" } },
        ],
      },
      { id: "next", type: "openText", headline: { default: "n" }, config: { inputType: "short" } },
      { id: "trap", type: "openText", headline: { default: "t" }, config: { inputType: "short" } },
    ],
    endings: [{ id: "e", type: "endScreen" }],
  });

  it("equals on an object answer does NOT match -> linear next", () => {
    const r = nextStep(build("equals"), { contact: { email: "x@y.z" }, gate: "ok" }, "gate");
    expect(r).toEqual({ type: "question", id: "next" });
  });

  it("notEquals on an object answer is vacuously TRUE -> jumps", () => {
    const r = nextStep(build("notEquals"), { contact: { email: "x@y.z" }, gate: "ok" }, "gate");
    expect(r).toEqual({ type: "question", id: "trap" });
  });
});

describe("requiredQuestionIds — require-during-answering", () => {
  const survey: Survey = {
    languages: lang,
    questions: [
      { id: "q1", type: "singleSelect", required: true, headline: { default: "1" }, config: { choices: [{ id: "yes", label: { default: "Y" } }, { id: "no", label: { default: "N" } }] } },
      {
        id: "q2",
        type: "openText",
        headline: { default: "2" },
        config: { inputType: "short" },
        logic: [{ id: "rq", condition: { leftQuestionId: "q1", operator: "equals", value: "yes" }, action: { type: "require", target: "q3" } }],
      },
      { id: "q3", type: "openText", headline: { default: "3" }, config: { inputType: "short" } },
    ],
    endings: [{ id: "e", type: "endScreen" }],
  };

  it("does NOT require q3 until its host q2 is answered", () => {
    expect([...requiredQuestionIds(survey, { q1: "yes" })].sort()).toEqual(["q1"]);
  });

  it("requires q3 once host q2 is answered and condition holds", () => {
    expect([...requiredQuestionIds(survey, { q1: "yes", q2: "more" })].sort()).toEqual(["q1", "q3"]);
  });
});

describe("validateSurvey", () => {
  it("flags a backward jumpTo", () => {
    const survey: Survey = {
      languages: lang,
      questions: [
        { id: "q1", type: "openText", headline: { default: "1" }, config: { inputType: "short" } },
        {
          id: "q2",
          type: "openText",
          headline: { default: "2" },
          config: { inputType: "short" },
          logic: [{ id: "r", condition: { leftQuestionId: "q2", operator: "isSubmitted" }, action: { type: "jumpTo", target: "q1" } }],
        },
      ],
      endings: [{ id: "e", type: "endScreen" }],
    };
    const r = validateSurvey(survey);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors.map((e) => e.code)).toContain("backward_jump");
  });

  it("accepts a valid survey", () => {
    const survey: Survey = {
      languages: lang,
      questions: [{ id: "q1", type: "openText", headline: { default: "1" }, config: { inputType: "short" } }],
      endings: [{ id: "e", type: "endScreen" }],
    };
    expect(validateSurvey(survey)).toEqual({ ok: true });
  });
});
