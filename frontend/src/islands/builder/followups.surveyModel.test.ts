// ============================================================================
// surveyModel follow-up transform tests (vitest).
//
// Covers the immutable follow-up CRUD helpers + the toSurveyWrite follow_ups carry:
//   - addFollowUp seeds a schema-shaped follow-up (responseFinished + sendEmail + i18n).
//   - removeFollowUp / updateFollowUp / setFollowUpName.
//   - setFollowUpRecipient (the discriminated recipient).
//   - setFollowUpCondition (null = unconditional; leaf = gated).
//   - setFollowUpSubject / setFollowUpBody (i18n-aware; preserve `default`).
//   - toSurveyWrite forwards survey.followUps under the snake_case follow_ups body key.
// ============================================================================
import { describe, expect, it } from "vitest";

import type { FollowUp, Question, Survey } from "../../lib/types";
import {
  addFollowUp,
  removeFollowUp,
  setFollowUpBody,
  setFollowUpCondition,
  setFollowUpName,
  setFollowUpRecipient,
  setFollowUpSubject,
  toSurveyWrite,
  updateFollowUp,
} from "./surveyModel";

function baseSurvey(questions: Question[] = []): Survey {
  return { name: "S", questions, languages: [{ code: "en", default: true }] };
}

const Q: Question = {
  id: "email",
  type: "contactInfo",
  headline: { default: "Email?" },
  config: { fields: { email: { show: true } } },
};

describe("addFollowUp", () => {
  it("seeds a schema-shaped unconditional sendEmail follow-up", () => {
    const s = addFollowUp(baseSurvey([Q]));
    expect(s.followUps).toHaveLength(1);
    const fu = s.followUps![0];
    expect(fu.id).toMatch(/^fu_/);
    expect(fu.trigger.type).toBe("responseFinished");
    expect(fu.trigger.condition).toBeUndefined(); // unconditional
    expect(fu.action.type).toBe("sendEmail");
    expect(fu.action.recipient).toEqual({ type: "fixedEmail", email: "" });
    expect(fu.action.subject.default).toBeTruthy();
    expect(fu.action.body.default).toContain("<p>");
  });

  it("does not mutate the source survey (immutability)", () => {
    const s0 = baseSurvey([Q]);
    const s1 = addFollowUp(s0);
    expect(s0.followUps).toBeUndefined();
    expect(s1).not.toBe(s0);
  });

  it("generates unique ids across multiple adds", () => {
    let s = baseSurvey([Q]);
    s = addFollowUp(s);
    s = addFollowUp(s);
    const ids = (s.followUps ?? []).map((f) => f.id);
    expect(new Set(ids).size).toBe(2);
  });
});

describe("removeFollowUp / updateFollowUp / setFollowUpName", () => {
  it("removes by id", () => {
    let s = addFollowUp(baseSurvey([Q]));
    const id = s.followUps![0].id;
    s = removeFollowUp(s, id);
    expect(s.followUps).toHaveLength(0);
  });

  it("remove no-ops (same ref) on an unknown id", () => {
    const s = addFollowUp(baseSurvey([Q]));
    expect(removeFollowUp(s, "nope")).toBe(s);
  });

  it("updates a follow-up in place", () => {
    let s = addFollowUp(baseSurvey([Q]));
    const fu = s.followUps![0];
    const edited: FollowUp = { ...fu, name: "Renamed" };
    s = updateFollowUp(s, edited);
    expect(s.followUps![0].name).toBe("Renamed");
  });

  it("setFollowUpName stores the name as typed and clears on whitespace-only", () => {
    let s = addFollowUp(baseSurvey([Q]));
    const id = s.followUps![0].id;
    // Stored as typed (interior spaces preserved; no mid-edit trimming).
    s = setFollowUpName(s, id, "Ops ping");
    expect(s.followUps![0].name).toBe("Ops ping");
    // Whitespace-only clears the label.
    s = setFollowUpName(s, id, "   ");
    expect(s.followUps![0].name).toBeUndefined();
  });
});

describe("setFollowUpRecipient", () => {
  it("switches between the three discriminated recipient forms", () => {
    let s = addFollowUp(baseSurvey([Q]));
    const id = s.followUps![0].id;

    s = setFollowUpRecipient(s, id, { type: "contact" });
    expect(s.followUps![0].action.recipient).toEqual({ type: "contact" });

    s = setFollowUpRecipient(s, id, { type: "responseField", questionId: "email" });
    expect(s.followUps![0].action.recipient).toEqual({
      type: "responseField",
      questionId: "email",
    });

    s = setFollowUpRecipient(s, id, { type: "fixedEmail", email: "ops@x.com" });
    expect(s.followUps![0].action.recipient).toEqual({ type: "fixedEmail", email: "ops@x.com" });
  });
});

describe("setFollowUpCondition", () => {
  it("sets a leaf condition (gated) then clears it (unconditional)", () => {
    let s = addFollowUp(baseSurvey([Q]));
    const id = s.followUps![0].id;

    s = setFollowUpCondition(s, id, {
      leftQuestionId: "email",
      operator: "isSubmitted",
    });
    expect(s.followUps![0].trigger.condition).toEqual({
      leftQuestionId: "email",
      operator: "isSubmitted",
    });
    expect(s.followUps![0].trigger.type).toBe("responseFinished");

    s = setFollowUpCondition(s, id, null);
    expect(s.followUps![0].trigger.condition).toBeUndefined();
    expect(s.followUps![0].trigger.type).toBe("responseFinished");
  });
});

describe("setFollowUpSubject / setFollowUpBody (i18n)", () => {
  it("default language writes the `default` key", () => {
    let s = addFollowUp(baseSurvey([Q]));
    const id = s.followUps![0].id;
    s = setFollowUpSubject(s, id, "en", "Hi there", "en");
    expect(s.followUps![0].action.subject.default).toBe("Hi there");
  });

  it("non-default language writes that lang key, preserving default", () => {
    let s = addFollowUp({
      name: "S",
      questions: [Q],
      languages: [
        { code: "en", default: true },
        { code: "fr", default: false },
      ],
    });
    const id = s.followUps![0].id;
    s = setFollowUpSubject(s, id, "en", "Hello", "en");
    s = setFollowUpSubject(s, id, "fr", "Bonjour", "en");
    expect(s.followUps![0].action.subject.default).toBe("Hello");
    expect(s.followUps![0].action.subject.fr).toBe("Bonjour");
  });

  it("body setter preserves HTML and is i18n-aware", () => {
    let s = addFollowUp(baseSurvey([Q]));
    const id = s.followUps![0].id;
    s = setFollowUpBody(s, id, "en", "<p>Thanks!</p>", "en");
    expect(s.followUps![0].action.body.default).toBe("<p>Thanks!</p>");
  });
});

describe("toSurveyWrite — follow_ups carry", () => {
  it("forwards survey.followUps under the snake_case follow_ups body key", () => {
    const s = addFollowUp(baseSurvey([Q]));
    const body = toSurveyWrite(s);
    expect(body.follow_ups).toEqual(s.followUps);
  });

  it("omits follow_ups when undefined (no key)", () => {
    const body = toSurveyWrite(baseSurvey([Q]));
    expect("follow_ups" in body).toBe(false);
  });
});
