// ============================================================================
// answer-gating helper tests.
// ============================================================================
import { describe, expect, it } from "vitest";

import { isEmptyAnswer, validateAnswer } from "./answers";
import type { Question } from "./types";

describe("isEmptyAnswer", () => {
  it("treats '', [], {}, null, undefined as empty", () => {
    expect(isEmptyAnswer("")).toBe(true);
    expect(isEmptyAnswer([])).toBe(true);
    expect(isEmptyAnswer({})).toBe(true);
    expect(isEmptyAnswer(null)).toBe(true);
    expect(isEmptyAnswer(undefined)).toBe(true);
  });

  it("treats 0 and false as NOT empty (NPS 0, unchecked-as-false)", () => {
    expect(isEmptyAnswer(0)).toBe(false);
    expect(isEmptyAnswer(false)).toBe(false);
  });

  it("treats an {choiceId} 'other' answer as empty only when choiceId is blank", () => {
    expect(isEmptyAnswer({ choiceId: "", other: "x" })).toBe(true);
    expect(isEmptyAnswer({ choiceId: "c", other: "" })).toBe(false);
  });
});

describe("validateAnswer", () => {
  const open = (cfg: Record<string, unknown> = {}): Question => ({
    id: "q",
    type: "openText",
    headline: { default: "h" },
    config: { inputType: "short", ...cfg },
  });

  it("requires a non-empty answer when required", () => {
    expect(validateAnswer(open(), undefined, true)).toMatch(/required/i);
    expect(validateAnswer(open(), "x", true)).toBeNull();
  });

  it("allows skipping an optional empty answer", () => {
    expect(validateAnswer(open(), undefined, false)).toBeNull();
  });

  it("enforces openText minLength", () => {
    expect(validateAnswer(open({ minLength: 5 }), "abc", true)).toMatch(/at least 5/i);
    expect(validateAnswer(open({ minLength: 5 }), "abcdef", true)).toBeNull();
  });

  it("enforces multiSelect min/max selections", () => {
    const ms: Question = {
      id: "q",
      type: "multiSelect",
      headline: { default: "h" },
      config: { choices: [], minSelections: 2, maxSelections: 3 },
    };
    expect(validateAnswer(ms, ["a"], false)).toMatch(/at least 2/i);
    expect(validateAnswer(ms, ["a", "b", "c", "d"], false)).toMatch(/at most 3/i);
    expect(validateAnswer(ms, ["a", "b"], false)).toBeNull();
  });

  it("requires 'other' text when a required isOther choice is picked", () => {
    const ss: Question = {
      id: "q",
      type: "singleSelect",
      headline: { default: "h" },
      config: { choices: [{ id: "o", label: { default: "Other" }, isOther: true }] },
    };
    expect(validateAnswer(ss, { choiceId: "o", other: "" }, true)).toMatch(/specify/i);
    expect(validateAnswer(ss, { choiceId: "o", other: "x" }, true)).toBeNull();
  });

  it("enforces number min/max", () => {
    const num = (cfg: Record<string, unknown>): Question => ({
      id: "q",
      type: "number",
      headline: { default: "h" },
      config: cfg,
    });
    expect(validateAnswer(num({ min: 1, max: 10 }), 0, false)).toMatch(/1 or more/i);
    expect(validateAnswer(num({ min: 1, max: 10 }), 11, false)).toMatch(/10 or less/i);
    expect(validateAnswer(num({ min: 1, max: 10 }), 5, false)).toBeNull();
    // 0 is a valid (non-empty) answer when within range.
    expect(validateAnswer(num({ min: 0 }), 0, true)).toBeNull();
  });

  it("enforces date min/max range", () => {
    const dt = (cfg: Record<string, unknown>): Question => ({
      id: "q",
      type: "date",
      headline: { default: "h" },
      config: cfg,
    });
    expect(validateAnswer(dt({ min: "2026-01-01" }), "2025-12-31", false)).toMatch(/on or after/i);
    expect(validateAnswer(dt({ max: "2026-12-31" }), "2027-01-01", false)).toMatch(/on or before/i);
    expect(validateAnswer(dt({ min: "2026-01-01", max: "2026-12-31" }), "2026-06-01", false)).toBeNull();
  });

  it("requires filled required contactInfo sub-fields (and a sane email)", () => {
    const ci: Question = {
      id: "q",
      type: "contactInfo",
      headline: { default: "h" },
      config: { fields: { email: { show: true, required: true }, company: { show: true } } },
    };
    // required email missing
    expect(validateAnswer(ci, { company: "Acme" }, false)).toMatch(/required contact/i);
    // bad email shape
    expect(validateAnswer(ci, { email: "nope" }, false)).toMatch(/valid email/i);
    // complete
    expect(validateAnswer(ci, { email: "ada@x.io" }, false)).toBeNull();
  });

  it("requires every row of a required matrix to be answered", () => {
    const mx: Question = {
      id: "q",
      type: "matrix",
      headline: { default: "h" },
      config: {
        selection: "single",
        rows: [
          { id: "r1", label: { default: "A" } },
          { id: "r2", label: { default: "B" } },
        ],
        columns: [
          { id: "c1", label: { default: "X" } },
          { id: "c2", label: { default: "Y" } },
        ],
      },
    };
    // only one row answered -> required matrix is incomplete
    expect(validateAnswer(mx, { r1: "c1" }, true)).toMatch(/every row/i);
    // both rows answered
    expect(validateAnswer(mx, { r1: "c1", r2: "c2" }, true)).toBeNull();
    // optional matrix: a partial answer is allowed
    expect(validateAnswer(mx, { r1: "c1" }, false)).toBeNull();
  });

  it("treats a complete ranking as valid and an empty required ranking as required", () => {
    const rk: Question = {
      id: "q",
      type: "ranking",
      headline: { default: "h" },
      config: {
        choices: [
          { id: "a", label: { default: "A" } },
          { id: "b", label: { default: "B" } },
        ],
      },
    };
    expect(validateAnswer(rk, ["a", "b"], true)).toBeNull();
    expect(validateAnswer(rk, undefined, true)).toMatch(/required/i);
  });

  it("requires filled required address sub-fields", () => {
    const ad: Question = {
      id: "q",
      type: "address",
      headline: { default: "h" },
      config: {
        fields: {
          addressLine1: { show: true, required: true },
          city: { show: true },
          state: { show: false },
        },
      },
    };
    // required addressLine1 missing
    expect(validateAnswer(ad, { city: "Berlin" }, false)).toMatch(/required address/i);
    // complete (required line filled)
    expect(validateAnswer(ad, { addressLine1: "1 Main St" }, false)).toBeNull();
    // a hidden field being absent is fine
    expect(validateAnswer(ad, { addressLine1: "1 Main St", city: "Berlin" }, true)).toBeNull();
  });

  it("treats a required pictureChoice as required and accepts single/multi shapes", () => {
    const single: Question = {
      id: "q",
      type: "pictureChoice",
      headline: { default: "h" },
      config: {
        options: [
          { id: "o1", imageUrl: "https://x/1.png" },
          { id: "o2", imageUrl: "https://x/2.png" },
        ],
        allowMulti: false,
      },
    };
    expect(validateAnswer(single, undefined, true)).toMatch(/required/i);
    expect(validateAnswer(single, "o1", true)).toBeNull();
    const multi: Question = { ...single, config: { ...single.config, allowMulti: true } };
    expect(validateAnswer(multi, [], true)).toMatch(/required/i);
    expect(validateAnswer(multi, ["o1", "o2"], true)).toBeNull();
  });

  it("treats a required scheduleMeeting as satisfied once a booking is recorded", () => {
    const sm: Question = {
      id: "q",
      type: "scheduleMeeting",
      headline: { default: "h" },
      config: { calLink: "jane" },
    };
    // not yet booked -> required not satisfied
    expect(validateAnswer(sm, undefined, true)).toMatch(/required/i);
    // booked -> satisfied
    expect(validateAnswer(sm, { booked: true, uid: "bk_1" }, true)).toBeNull();
    // optional + unbooked -> skippable
    expect(validateAnswer(sm, undefined, false)).toBeNull();
  });

  it("treats an optional empty fileUpload as skippable (backend deferred)", () => {
    const fu: Question = {
      id: "q",
      type: "fileUpload",
      headline: { default: "h" },
      config: { allowMultiple: false },
    };
    // No upload endpoint yet -> answer is always empty; optional is fine.
    expect(validateAnswer(fu, undefined, false)).toBeNull();
    // A required fileUpload is (correctly) not satisfiable until the backend lands.
    expect(validateAnswer(fu, undefined, true)).toMatch(/required/i);
  });
});
