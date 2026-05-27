// ============================================================================
// Builder QUOTAS model tests (feat/parity-quotas).
//
// Covers the quota CRUD helpers (addQuota / removeQuota / setQuotaName / setQuotaLimit /
// setQuotaCondition / setQuotaEndingId) and that toSurveyWrite forwards `quotas`. Every helper
// keeps the survey schema-valid (the real shared validateSurvey doesn't touch quotas — server-only
// cross-refs — so we assert structural invariants the helpers guarantee: limit >= 1, condition
// shape, endingId set/cleared). Reference-inequality is asserted so the builder dirty-check stays
// honest; no-ops return the SAME survey reference.
// ============================================================================
import { describe, expect, it } from "vitest";

import type { Survey } from "../../lib/types";
import {
  addQuota,
  removeQuota,
  setQuotaCondition,
  setQuotaEndingId,
  setQuotaLimit,
  setQuotaName,
  toSurveyWrite,
} from "./surveyModel";

function baseSurvey(): Survey {
  return {
    name: "S",
    languages: [{ code: "en", default: true }],
    questions: [
      {
        id: "country",
        type: "singleSelect",
        headline: { default: "Country?" },
        config: { choices: [{ id: "us", label: { default: "US" } }, { id: "uk", label: { default: "UK" } }] },
      },
    ],
    endings: [
      { id: "end1", type: "endScreen", headline: { default: "Thanks" } },
      { id: "quota_full", type: "endScreen", headline: { default: "Full" } },
    ],
    hiddenFields: [{ id: "plan", name: "Plan" }],
  };
}

describe("addQuota", () => {
  it("appends a schema-shaped quota (limit, leaf condition, no endingId) against the first source", () => {
    const s0 = baseSurvey();
    const s1 = addQuota(s0, "US cap");
    expect(s1).not.toBe(s0); // fresh object (dirty-check honest)
    expect(s1.quotas).toHaveLength(1);
    const q = s1.quotas![0];
    expect(q.name).toBe("US cap");
    expect(q.limit).toBe(100);
    expect("connector" in q.condition).toBe(false); // a single leaf
    expect((q.condition as { leftQuestionId: string }).leftQuestionId).toBe("country");
    expect(q.endingId).toBeUndefined(); // built-in screen until the author picks one
    expect(q.id).toMatch(/^quota_/);
  });

  it("generates unique ids across multiple adds", () => {
    let s = baseSurvey();
    s = addQuota(s);
    s = addQuota(s);
    const ids = (s.quotas ?? []).map((q) => q.id);
    expect(new Set(ids).size).toBe(2);
  });

  it("falls back to a hidden-field source when there are no questions", () => {
    const s0: Survey = { ...baseSurvey(), questions: [] };
    const s1 = addQuota(s0);
    expect((s1.quotas![0].condition as { leftQuestionId: string }).leftQuestionId).toBe("plan");
  });
});

describe("removeQuota", () => {
  it("removes by id", () => {
    let s = addQuota(baseSurvey());
    const id = s.quotas![0].id;
    s = removeQuota(s, id);
    expect(s.quotas).toHaveLength(0);
  });

  it("no-ops (same reference) for an unknown id", () => {
    const s = addQuota(baseSurvey());
    expect(removeQuota(s, "nope")).toBe(s);
  });
});

describe("setQuotaName", () => {
  it("stores the name as typed (no mid-edit trim — controlled-input friendly)", () => {
    let s = addQuota(baseSurvey(), "x");
    const id = s.quotas![0].id;
    s = setQuotaName(s, id, "US respondents");
    expect(s.quotas![0].name).toBe("US respondents");
    // A whitespace-only value is stored as-is (server validation rejects an empty name on save).
    s = setQuotaName(s, id, "   ");
    expect(s.quotas![0].name).toBe("   ");
  });
});

describe("setQuotaLimit", () => {
  it("coerces to an integer >= 1", () => {
    let s = addQuota(baseSurvey());
    const id = s.quotas![0].id;
    s = setQuotaLimit(s, id, "5");
    expect(s.quotas![0].limit).toBe(5);
    s = setQuotaLimit(s, id, "2.9");
    expect(s.quotas![0].limit).toBe(2); // floored
    s = setQuotaLimit(s, id, "0");
    expect(s.quotas![0].limit).toBe(1); // clamped to >= 1
    s = setQuotaLimit(s, id, "");
    expect(s.quotas![0].limit).toBe(1); // blank/NaN -> 1
    s = setQuotaLimit(s, id, -3);
    expect(s.quotas![0].limit).toBe(1);
  });
});

describe("setQuotaCondition", () => {
  it("replaces the matching condition (leaf or connector)", () => {
    let s = addQuota(baseSurvey());
    const id = s.quotas![0].id;
    s = setQuotaCondition(s, id, { leftQuestionId: "country", operator: "equals", value: "uk" });
    expect((s.quotas![0].condition as { value: string }).value).toBe("uk");
    // A connector condition is accepted as-is.
    s = setQuotaCondition(s, id, {
      connector: "and",
      conditions: [{ leftQuestionId: "country", operator: "equals", value: "us" }],
    });
    expect("connector" in s.quotas![0].condition).toBe(true);
  });
});

describe("setQuotaEndingId", () => {
  it("sets and clears the over-quota ending", () => {
    let s = addQuota(baseSurvey());
    const id = s.quotas![0].id;
    s = setQuotaEndingId(s, id, "quota_full");
    expect(s.quotas![0].endingId).toBe("quota_full");
    s = setQuotaEndingId(s, id, undefined);
    expect(s.quotas![0].endingId).toBeUndefined();
    s = setQuotaEndingId(s, id, "quota_full");
    s = setQuotaEndingId(s, id, ""); // empty string clears (built-in screen)
    expect(s.quotas![0].endingId).toBeUndefined();
  });
});

describe("toSurveyWrite", () => {
  it("forwards quotas under the same `quotas` key (no rename)", () => {
    const s = addQuota(baseSurvey(), "US");
    const body = toSurveyWrite(s);
    expect(body.quotas).toEqual(s.quotas);
  });

  it("omits quotas entirely when the survey has none (byte-identical to today)", () => {
    const s = baseSurvey();
    expect(s.quotas).toBeUndefined();
    const body = toSurveyWrite(s);
    expect("quotas" in body).toBe(false);
  });
});
