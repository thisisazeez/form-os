// ============================================================================
// Tests for the shared recall/piping resolver (lib/recall.ts).
// Token format (FROZEN): #recall:<id>/fallback:<text>#
// ============================================================================
import { describe, expect, it } from "vitest";

import { hasRecallToken, RECALL_TOKEN_RE, resolveRecall } from "./recall";
import type { AnswerMap, HiddenField } from "./types";

describe("resolveRecall", () => {
  it("returns no-token text unchanged (single-survey behavior identical)", () => {
    expect(resolveRecall("Plain headline", {})).toBe("Plain headline");
    expect(resolveRecall("What is your favorite color?", { q1: "blue" })).toBe(
      "What is your favorite color?",
    );
  });

  it("returns empty/undefined text as empty string", () => {
    expect(resolveRecall("", {})).toBe("");
    expect(resolveRecall(undefined, {})).toBe("");
    expect(resolveRecall(null, {})).toBe("");
  });

  it("replaces a token with the live answer value", () => {
    const answers: AnswerMap = { name: "Ada" };
    expect(resolveRecall("Hi #recall:name/fallback:there#!", answers)).toBe("Hi Ada!");
  });

  it("uses the fallback when the source is unanswered", () => {
    expect(resolveRecall("Hi #recall:name/fallback:there#!", {})).toBe("Hi there!");
  });

  it("uses the fallback when the source answer is empty", () => {
    // Empty string, empty array, empty object all count as unanswered.
    expect(resolveRecall("Hi #recall:name/fallback:guest#", { name: "" })).toBe("Hi guest");
    expect(resolveRecall("Picks: #recall:q/fallback:none#", { q: [] })).toBe("Picks: none");
    expect(resolveRecall("Addr: #recall:q/fallback:n/a#", { q: {} })).toBe("Addr: n/a");
  });

  it("supports an empty fallback", () => {
    expect(resolveRecall("Hello #recall:name/fallback:#", {})).toBe("Hello ");
    expect(resolveRecall("Hello #recall:name/fallback:#", { name: "Ada" })).toBe("Hello Ada");
  });

  it("replaces multiple tokens in one string", () => {
    const answers: AnswerMap = { first: "Ada", plan: "pro" };
    const text = "Hi #recall:first/fallback:friend#, on the #recall:plan/fallback:free# plan.";
    expect(resolveRecall(text, answers)).toBe("Hi Ada, on the pro plan.");
  });

  it("mixes a present and an absent source across tokens", () => {
    const answers: AnswerMap = { first: "Ada" };
    const text = "Hi #recall:first/fallback:friend# (#recall:plan/fallback:free#)";
    expect(resolveRecall(text, answers)).toBe("Hi Ada (free)");
  });

  it("recalls a hidden-field value (captured into answers under its id)", () => {
    // Hidden field values live in the SAME answers map (the runtime captures them
    // from the URL); resolution is by id, same as a question answer.
    const hiddenFields: HiddenField[] = [{ id: "userId", name: "User ID" }];
    const answers: AnswerMap = { userId: "123" };
    expect(
      resolveRecall("Welcome back, user #recall:userId/fallback:guest#", answers, hiddenFields),
    ).toBe("Welcome back, user 123");
    // And falls back when the hidden field was not supplied.
    expect(
      resolveRecall("Welcome back, user #recall:userId/fallback:guest#", {}, hiddenFields),
    ).toBe("Welcome back, user guest");
  });

  it("renders number, boolean, and array answer values", () => {
    expect(resolveRecall("Score: #recall:nps/fallback:?#", { nps: 9 })).toBe("Score: 9");
    expect(resolveRecall("Agree: #recall:c/fallback:?#", { c: true })).toBe("Agree: Yes");
    expect(resolveRecall("Agree: #recall:c/fallback:?#", { c: false })).toBe("Agree: No");
    expect(resolveRecall("Picks: #recall:m/fallback:?#", { m: ["a", "b"] })).toBe("Picks: a, b");
  });

  it("renders an 'Other' choice answer's typed text", () => {
    const answers: AnswerMap = { sel: { choiceId: "other", other: "Custom thing" } };
    expect(resolveRecall("You said: #recall:sel/fallback:nothing#", answers)).toBe(
      "You said: Custom thing",
    );
  });

  it("falls back for un-pipeable object-shaped answers (contactInfo/address)", () => {
    const answers: AnswerMap = { contact: { firstName: "Ada", email: "a@x.com" } };
    expect(resolveRecall("Hi #recall:contact/fallback:friend#", answers)).toBe("Hi friend");
  });

  it("ignores a malformed token (missing the /fallback: part) — left as literal text", () => {
    const text = "Broken #recall:name# token";
    expect(resolveRecall(text, { name: "Ada" })).toBe("Broken #recall:name# token");
  });

  it("does not treat a '#' inside would-be fallback text as part of the token", () => {
    // The fallback charset excludes '#'; "#recall:a/fallback:x#y#" matches with fallback "x"
    // and leaves the trailing "y#" as literal text.
    expect(resolveRecall("#recall:a/fallback:x#y#", {})).toBe("xy#");
  });

  it("works on a fresh map with prefilled hidden values only", () => {
    const answers: AnswerMap = { plan: "enterprise" };
    expect(resolveRecall("Plan: #recall:plan/fallback:free#", answers)).toBe("Plan: enterprise");
  });
});

describe("hasRecallToken", () => {
  it("detects a token", () => {
    expect(hasRecallToken("Hi #recall:name/fallback:x#")).toBe(true);
  });
  it("returns false for plain text / non-strings", () => {
    expect(hasRecallToken("Plain text")).toBe(false);
    expect(hasRecallToken(undefined)).toBe(false);
    expect(hasRecallToken(null)).toBe(false);
    expect(hasRecallToken("#recall:bad#")).toBe(false);
  });
});

describe("RECALL_TOKEN_RE", () => {
  it("is global and resets between resolveRecall calls (no lastIndex bleed)", () => {
    // The global flag means lastIndex is stateful; resolveRecall uses String.replace
    // which resets it. Confirm two sequential resolves are consistent.
    const text = "#recall:a/fallback:A#";
    expect(resolveRecall(text, { a: "x" })).toBe("x");
    expect(resolveRecall(text, { a: "y" })).toBe("y");
    // The exported regex carries the global flag.
    expect(RECALL_TOKEN_RE.flags).toContain("g");
  });
});
