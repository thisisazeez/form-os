// ============================================================================
// Logic-model unit tests (pure helpers + the SCHEMA-VALIDITY guarantee).
//
// The load-bearing contract: every rule the editor emits must pass the SHARED
// validateSurvey (../../../lib/logic) — the same validator the server runs — so
// Save (PUT) never 400s on logic. These tests:
//   • Pin the builder-local OPERATOR_VALIDITY mirror to the REAL validateSurvey:
//     every operator the mirror offers for a type is accepted by validateSurvey;
//     none triggers `invalid_operator`.
//   • Prove makeRule / setRule produce schema-valid surveys (validateSurvey ok).
//   • Prove the host invariant: a forward condition re-hosts the rule, and the
//     stored rule lives in the right question's logic[].
//   • Cover reorder (within-host), removeRule, and flattenRules.
// ============================================================================
import { describe, expect, it } from "vitest";

import { validateSurvey } from "../../../lib/logic";
import type { Operator, Question, QuestionType, Survey } from "../../../lib/types";
import {
  defaultOperatorForType,
  flattenRules,
  hostIndexForRule,
  isOperatorValidForType,
  makeRule,
  OPERATOR_VALIDITY,
  operatorTakesValue,
  removeRule,
  reorderRule,
  ruleLeaves,
  setLeafOperator,
  setLeafQuestion,
  setRule,
  valueKind,
} from "./logicModel";

// --------------------------------------------------------------------------- //
//  Survey fixtures                                                            //
// --------------------------------------------------------------------------- //
function q(id: string, type: QuestionType, extra: Partial<Question> = {}): Question {
  const base: Question = { id, type, headline: { default: id }, ...extra };
  // Give choice types a schema-valid config so validateSurvey on the whole survey
  // doesn't trip on missing choices when we attach logic.
  if (type === "singleSelect" || type === "multiSelect") {
    base.config = {
      choices: [
        { id: "a", label: { default: "A" } },
        { id: "b", label: { default: "B" } },
      ],
    };
  } else if (type === "rating") {
    base.config = { range: 5, scale: "star" };
  } else if (type === "consent") {
    base.config = { label: { default: "I agree" } };
  } else {
    base.config = {};
  }
  return base;
}

function survey(questions: Question[], endings: Survey["endings"] = []): Survey {
  return { name: "T", questions, languages: [{ code: "en", default: true }], endings };
}

const THREE_Q = () =>
  survey(
    [q("q1", "openText"), q("q2", "nps"), q("q3", "openText")],
    [{ id: "end1", type: "endScreen", headline: { default: "Thanks" } }],
  );

// --------------------------------------------------------------------------- //
//  OPERATOR_VALIDITY mirror is pinned to the real validateSurvey              //
// --------------------------------------------------------------------------- //
describe("OPERATOR_VALIDITY mirror ↔ validateSurvey", () => {
  // For each question type, host a rule on a LATER question whose condition refs an
  // EARLIER question of the type-under-test, using each operator the mirror offers.
  // validateSurvey must NOT raise `invalid_operator` for any of them.
  const TYPES: QuestionType[] = [
    "openText", "singleSelect", "multiSelect", "rating", "nps", "number",
    "date", "consent", "cta", "matrix", "ranking", "contactInfo", "hidden", "pictureChoice",
  ];

  for (const t of TYPES) {
    it(`every offered operator for "${t}" is accepted by validateSurvey`, () => {
      const ops = OPERATOR_VALIDITY[t];
      for (const op of ops) {
        // Survey: [refQ(type t), hostQ(openText with the rule), end]
        const refQ = q("ref", t);
        // A multiChoice/singleChoice value uses real choice ids when present.
        const value = operatorTakesValue(op)
          ? exampleValue(t, op)
          : undefined;
        const hostQ: Question = {
          id: "host",
          type: "openText",
          headline: { default: "host" },
          config: {},
          logic: [
            {
              id: "r1",
              condition:
                value === undefined
                  ? { leftQuestionId: "ref", operator: op }
                  : { leftQuestionId: "ref", operator: op, value },
              action: { type: "endSurvey" },
            },
          ],
        };
        const s = survey([refQ, hostQ], [{ id: "e", type: "endScreen" }]);
        const result = validateSurvey(s);
        const invalidOp =
          !result.ok && result.errors.some((e) => e.code === "invalid_operator");
        expect(invalidOp, `operator ${op} on type ${t} should be valid`).toBe(false);
      }
    });
  }

  function exampleValue(t: QuestionType, op: Operator): string | number | (string | number)[] {
    const kind = valueKind(t, op);
    if (kind === "multiChoice") return ["a"];
    if (kind === "singleChoice") return "a";
    if (kind === "number") return 3;
    if (kind === "date") return "2020-01-01";
    return "x";
  }

  it("offers NO operators for fileUpload (matches the validator's empty set)", () => {
    expect(OPERATOR_VALIDITY.fileUpload).toEqual([]);
  });

  it("isOperatorValidForType agrees with the table", () => {
    expect(isOperatorValidForType("nps", "lessThan")).toBe(true);
    expect(isOperatorValidForType("nps", "contains")).toBe(false);
    expect(isOperatorValidForType("openText", "contains")).toBe(true);
  });
});

// --------------------------------------------------------------------------- //
//  makeRule / setRule produce schema-valid surveys                            //
// --------------------------------------------------------------------------- //
describe("makeRule + setRule → validateSurvey ok", () => {
  it("makeRule returns null for a survey with no questions", () => {
    expect(makeRule(survey([]))).toBeNull();
  });

  it("a freshly-added rule passes validateSurvey", () => {
    const s = THREE_Q();
    const rule = makeRule(s)!;
    const next = setRule(s, rule);
    const result = validateSurvey(next);
    expect(result.ok).toBe(true);
  });

  it("seeds the rule on questions[0] with isSubmitted + a forward jump", () => {
    const s = THREE_Q();
    const rule = makeRule(s)!;
    const leaves = ruleLeaves(rule);
    expect(leaves).toHaveLength(1);
    expect(leaves[0].leftQuestionId).toBe("q1");
    expect(leaves[0].operator).toBe("isSubmitted");
    // default action jumps to the next question (q2).
    expect(rule.action).toEqual({ type: "jumpTo", target: "q2" });
  });

  it("stores the rule in its host question's logic[] (host = q1 for a q1 condition)", () => {
    const s = THREE_Q();
    const rule = makeRule(s)!;
    const next = setRule(s, rule);
    const q1 = next.questions.find((x) => x.id === "q1")!;
    expect(q1.logic).toHaveLength(1);
    expect(q1.logic![0].id).toBe(rule.id);
    // other questions untouched
    expect(next.questions.find((x) => x.id === "q2")!.logic ?? []).toHaveLength(0);
  });
});

// --------------------------------------------------------------------------- //
//  Host invariant: a forward condition re-hosts the rule                      //
// --------------------------------------------------------------------------- //
describe("host derivation + re-hosting", () => {
  it("hostIndexForRule = max condition-ref index", () => {
    const s = THREE_Q();
    const rule = {
      id: "r",
      condition: {
        connector: "and" as const,
        conditions: [
          { leftQuestionId: "q1", operator: "isSubmitted" as Operator },
          { leftQuestionId: "q2", operator: "isSubmitted" as Operator },
        ],
      },
      action: { type: "endSurvey" as const },
    };
    expect(hostIndexForRule(s, rule)).toBe(1); // q2 is index 1
  });

  it("setRule moves the rule to the LATER host when a condition is added", () => {
    const s = THREE_Q();
    // Start hosted on q1.
    let rule = makeRule(s)!;
    let next = setRule(s, rule);
    expect(next.questions.find((x) => x.id === "q1")!.logic).toHaveLength(1);

    // Add a condition referencing q2 → host migrates to q2.
    rule = {
      ...rule,
      condition: {
        connector: "and",
        conditions: [
          { leftQuestionId: "q1", operator: "isSubmitted" },
          { leftQuestionId: "q2", operator: "isSubmitted" },
        ],
      },
    };
    next = setRule(next, rule);
    expect(next.questions.find((x) => x.id === "q1")!.logic ?? []).toHaveLength(0);
    expect(next.questions.find((x) => x.id === "q2")!.logic).toHaveLength(1);
    // The jump (to q2) is now AT the host → still after? q2->q2 is NOT after, so this
    // is exactly the re-host hazard. validateSurvey must catch it.
    const result = validateSurvey(next);
    expect(result.ok).toBe(false);
    expect(!result.ok && result.errors.some((e) => e.code === "backward_jump")).toBe(true);
  });
});

// --------------------------------------------------------------------------- //
//  leaf edits keep the leaf schema-shaped                                     //
// --------------------------------------------------------------------------- //
describe("leaf edits", () => {
  it("setLeafQuestion picks the type's default operator and drops the value", () => {
    const leaf = setLeafQuestion("q2", "nps");
    expect(leaf.leftQuestionId).toBe("q2");
    expect(leaf.operator).toBe(defaultOperatorForType("nps"));
  });

  it("switching to a valueless operator drops the value key", () => {
    const start = setLeafQuestion("q2", "nps"); // numeric default (equals) → has value 0
    const next = setLeafOperator(start, "isSubmitted", "nps");
    expect("value" in next).toBe(false);
  });

  it("switching to a numeric operator coerces a numeric value", () => {
    const start = setLeafQuestion("q2", "nps");
    const next = setLeafOperator(start, "greaterThan", "nps");
    expect(typeof next.value).toBe("number");
  });
});

// --------------------------------------------------------------------------- //
//  reorder / remove / flatten                                                 //
// --------------------------------------------------------------------------- //
describe("reorder / remove / flatten", () => {
  function withTwoRulesOnQ1(): Survey {
    const s = THREE_Q();
    const r1 = {
      id: "r1",
      condition: { leftQuestionId: "q1", operator: "isSubmitted" as Operator },
      action: { type: "jumpTo" as const, target: "q2" },
    };
    const r2 = {
      id: "r2",
      condition: { leftQuestionId: "q1", operator: "isSubmitted" as Operator },
      action: { type: "jumpTo" as const, target: "q3" },
    };
    return setRule(setRule(s, r1), r2);
  }

  it("reorderRule swaps two rules within the same host (precedence)", () => {
    const s = withTwoRulesOnQ1();
    const before = (s.questions.find((x) => x.id === "q1")!.logic ?? []).map((r) => r.id);
    expect(before).toEqual(["r1", "r2"]);

    const moved = reorderRule(s, "r2", -1);
    const after = (moved.questions.find((x) => x.id === "q1")!.logic ?? []).map((r) => r.id);
    expect(after).toEqual(["r2", "r1"]);
  });

  it("reorderRule is a no-op at the edge of a host's block", () => {
    const s = withTwoRulesOnQ1();
    const moved = reorderRule(s, "r1", -1); // r1 already first
    expect(moved).toBe(s); // same reference (no-op) is acceptable here
  });

  it("removeRule deletes the rule from its host", () => {
    const s = withTwoRulesOnQ1();
    const next = removeRule(s, "r1");
    const ids = (next.questions.find((x) => x.id === "q1")!.logic ?? []).map((r) => r.id);
    expect(ids).toEqual(["r2"]);
  });

  it("flattenRules returns the survey-wide list with host tags, ordered by host", () => {
    const s = withTwoRulesOnQ1();
    const flat = flattenRules(s);
    expect(flat.map((f) => f.rule.id)).toEqual(["r1", "r2"]);
    expect(flat.every((f) => f.hostQuestionId === "q1")).toBe(true);
    expect(flat[0].hostIndex).toBe(0);
  });
});
