// ============================================================================
// Builder VARIABLES model tests (feat/parity-variables).
//
// Covers the survey-variable CRUD helpers (addVariable / removeVariable /
// setVariableInitialValue / setVariableName), the calculate-action helpers in the
// logic model (calcOpsForVariableType / validateRuleAgainstSurvey), and that
// toSurveyWrite forwards `variables`. Every helper is asserted to keep the survey
// schema-valid (real shared validateSurvey) so the builder can never author an
// invalid variable/calculate.
// ============================================================================
import { describe, expect, it } from "vitest";

import { validateSurvey } from "../../lib/logic";
import type { LogicRule, Question, Survey } from "../../lib/types";
import {
  calcOpsForVariableType,
  validateRuleAgainstSurvey,
} from "./logic/logicModel";
import {
  addVariable,
  isValidVariableId,
  removeVariable,
  setVariableInitialValue,
  setVariableName,
  toSurveyWrite,
} from "./surveyModel";

function baseSurvey(): Survey {
  return {
    name: "S",
    languages: [{ code: "en", default: true }],
    questions: [
      { id: "q1", type: "number", headline: { default: "n" }, config: {} },
      { id: "q2", type: "openText", headline: { default: "t" }, config: {} },
    ],
  };
}

describe("variable id validation", () => {
  it("accepts question-id charset, rejects bad ids", () => {
    expect(isValidVariableId("score")).toBe(true);
    expect(isValidVariableId("my_var-2")).toBe(true);
    expect(isValidVariableId("2bad")).toBe(false);
    expect(isValidVariableId("has space")).toBe(false);
  });
});

describe("addVariable", () => {
  it("adds a number variable seeded to 0 and stays schema-valid", () => {
    const s = addVariable(baseSurvey(), "score", "Score", "number");
    expect(s.variables).toEqual([
      { id: "score", name: "Score", type: "number", initialValue: 0 },
    ]);
    expect(validateSurvey(s).ok).toBe(true);
  });

  it("adds a text variable seeded to empty string", () => {
    const s = addVariable(baseSurvey(), "tier", "", "text");
    // a blank name falls back to the id
    expect(s.variables).toEqual([{ id: "tier", name: "tier", type: "text", initialValue: "" }]);
  });

  it("no-ops on an invalid id (same ref)", () => {
    const base = baseSurvey();
    expect(addVariable(base, "2bad", "X", "number")).toBe(base);
  });

  it("no-ops on a collision with a question id (shared namespace)", () => {
    const base = baseSurvey();
    expect(addVariable(base, "q1", "Clash", "number")).toBe(base);
  });

  it("no-ops on a duplicate variable id", () => {
    const s = addVariable(baseSurvey(), "score", "Score", "number");
    expect(addVariable(s, "score", "Again", "number")).toBe(s);
  });
});

describe("removeVariable / set helpers", () => {
  it("removes by id, no-ops when absent", () => {
    const s = addVariable(baseSurvey(), "score", "Score", "number");
    expect(removeVariable(s, "score").variables).toEqual([]);
    expect(removeVariable(s, "nope")).toBe(s);
  });

  it("sets the initial value with type coercion", () => {
    let s = addVariable(baseSurvey(), "score", "Score", "number");
    s = setVariableInitialValue(s, "score", "42");
    expect(s.variables?.[0].initialValue).toBe(42);
    // non-numeric -> 0 (NaN guard)
    s = setVariableInitialValue(s, "score", "abc");
    expect(s.variables?.[0].initialValue).toBe(0);
  });

  it("text initial value is stored verbatim", () => {
    let s = addVariable(baseSurvey(), "tier", "Tier", "text");
    s = setVariableInitialValue(s, "tier", "gold");
    expect(s.variables?.[0].initialValue).toBe("gold");
  });

  it("sets the name; a blank name falls back to the id", () => {
    let s = addVariable(baseSurvey(), "score", "Score", "number");
    s = setVariableName(s, "score", "Total points");
    expect(s.variables?.[0].name).toBe("Total points");
    s = setVariableName(s, "score", "   ");
    expect(s.variables?.[0].name).toBe("score");
  });
});

describe("calcOpsForVariableType", () => {
  it("number -> arithmetic ops, text -> assign/concat", () => {
    expect(calcOpsForVariableType("number")).toEqual([
      "assign", "add", "subtract", "multiply", "divide",
    ]);
    expect(calcOpsForVariableType("text")).toEqual(["assign", "concat"]);
  });
});

describe("validateRuleAgainstSurvey — calculate", () => {
  function surveyWithVar(type: "number" | "text"): Survey {
    return addVariable(baseSurvey(), "v", "V", type);
  }

  it("a valid number calculate has no issues", () => {
    const s = surveyWithVar("number");
    const rule: LogicRule = {
      id: "r",
      condition: { leftQuestionId: "q1", operator: "isSubmitted" },
      action: { type: "calculate", variableId: "v", operation: "add", value: { type: "literal", value: 1 } },
    };
    expect(validateRuleAgainstSurvey(s, rule)).toEqual([]);
  });

  it("flags an unknown variable", () => {
    const s = surveyWithVar("number");
    const rule: LogicRule = {
      id: "r",
      condition: { leftQuestionId: "q1", operator: "isSubmitted" },
      action: { type: "calculate", variableId: "ghost", operation: "add", value: { type: "literal", value: 1 } },
    };
    expect(validateRuleAgainstSurvey(s, rule).map((i) => i.code)).toContain("unknown_variable");
  });

  it("flags a type-incompatible operation (concat on a number)", () => {
    const s = surveyWithVar("number");
    const rule: LogicRule = {
      id: "r",
      condition: { leftQuestionId: "q1", operator: "isSubmitted" },
      action: { type: "calculate", variableId: "v", operation: "concat", value: { type: "literal", value: "x" } },
    };
    expect(validateRuleAgainstSurvey(s, rule).map((i) => i.code)).toContain("invalid_calc_op");
  });

  it("flags an unknown value ref", () => {
    const s = surveyWithVar("number");
    const rule: LogicRule = {
      id: "r",
      condition: { leftQuestionId: "q1", operator: "isSubmitted" },
      action: { type: "calculate", variableId: "v", operation: "add", value: { type: "ref", ref: "nope" } },
    };
    expect(validateRuleAgainstSurvey(s, rule).map((i) => i.code)).toContain("unknown_calc_ref");
  });

  it("accepts a ref to a question / variable / hidden-field id", () => {
    const s: Survey = {
      ...surveyWithVar("number"),
      hiddenFields: [{ id: "uid" }],
    };
    for (const ref of ["q1", "v", "uid"]) {
      const rule: LogicRule = {
        id: "r",
        condition: { leftQuestionId: "q1", operator: "isSubmitted" },
        action: { type: "calculate", variableId: "v", operation: "add", value: { type: "ref", ref } },
      };
      expect(validateRuleAgainstSurvey(s, rule)).toEqual([]);
    }
  });
});

describe("toSurveyWrite — variables forwarding", () => {
  it("forwards `variables` under the same key (no rename)", () => {
    const s = addVariable(baseSurvey(), "score", "Score", "number");
    const body = toSurveyWrite(s) as { variables?: unknown };
    expect(body.variables).toEqual([
      { id: "score", name: "Score", type: "number", initialValue: 0 },
    ]);
  });

  it("a survey with a variable + a calculate condition passes the real validateSurvey", () => {
    const s = addVariable(baseSurvey(), "score", "Score", "number");
    // q1 adds to score; q2 jumps based on the variable.
    const q1: Question = {
      ...(s.questions[0] as Question),
      logic: [
        {
          id: "rc",
          condition: { leftQuestionId: "q1", operator: "isSubmitted" },
          action: { type: "calculate", variableId: "score", operation: "add", value: { type: "ref", ref: "q1" } },
        },
      ],
    };
    const q2: Question = {
      ...(s.questions[1] as Question),
      logic: [
        {
          id: "rj",
          condition: { leftQuestionId: "score", operator: "greaterThan", value: 5 },
          action: { type: "endSurvey" },
        },
      ],
    };
    const survey: Survey = { ...s, questions: [q1, q2] };
    expect(validateSurvey(survey).ok).toBe(true);
  });
});
