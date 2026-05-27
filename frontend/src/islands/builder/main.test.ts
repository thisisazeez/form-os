// ============================================================================
// Builder entry — initial-survey resolution tests.
//
// The mount contract supports BOTH the raw-JSON form (data-initial-survey='{...}')
// AND the json_script-id form (Django {{ x|json_script:"id" }}), addressed either
// via data-config-id or by data-initial-survey carrying the element id.
// ============================================================================
import { afterEach, describe, expect, it } from "vitest";

import type { Survey } from "../../lib/types";
import { normalizeInitialSurvey, resolveInitialSurvey } from "./main";

const SURVEY: Survey = {
  name: "Injected",
  questions: [{ id: "q1", type: "openText", headline: { default: "Hi" }, config: {} }],
  languages: [{ code: "en", default: true }],
};

function mountDiv(attrs: Record<string, string>): HTMLElement {
  const el = document.createElement("div");
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  document.body.appendChild(el);
  return el;
}

function jsonScript(id: string, payload: unknown): void {
  const s = document.createElement("script");
  s.type = "application/json";
  s.id = id;
  s.textContent = JSON.stringify(payload);
  document.body.appendChild(s);
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("resolveInitialSurvey", () => {
  it("parses raw inline JSON from data-initial-survey", () => {
    const el = mountDiv({ "data-initial-survey": JSON.stringify(SURVEY) });
    expect(resolveInitialSurvey(el)?.name).toBe("Injected");
  });

  it("reads a json_script payload referenced by data-config-id", () => {
    jsonScript("opensurvey-builder-config", SURVEY);
    const el = mountDiv({ "data-config-id": "opensurvey-builder-config" });
    expect(resolveInitialSurvey(el)?.questions[0].id).toBe("q1");
  });

  it("treats a non-JSON data-initial-survey value as a json_script element id", () => {
    jsonScript("my-payload", SURVEY);
    const el = mountDiv({ "data-initial-survey": "my-payload" });
    expect(resolveInitialSurvey(el)?.name).toBe("Injected");
  });

  it("returns null when nothing usable is injected (caller falls back to GET)", () => {
    const el = mountDiv({});
    expect(resolveInitialSurvey(el)).toBeNull();
  });

  it("returns null for malformed inline JSON that is not an element id", () => {
    const el = mountDiv({ "data-initial-survey": "{ not json" });
    expect(resolveInitialSurvey(el)).toBeNull();
  });
});

describe("normalizeInitialSurvey — snake→camel hidden_fields bridge", () => {
  it("maps the serializer's snake_case hidden_fields onto camelCase hiddenFields", () => {
    // The authenticated SurveySerializer injects `hidden_fields` (snake); the builder
    // edits `hiddenFields` (camel). Without this bridge an existing survey's hidden
    // fields would render empty and be wiped on the next Save.
    const loaded = {
      ...SURVEY,
      hidden_fields: [{ id: "userId", name: "User ID" }],
    } as unknown as Survey;
    const out = normalizeInitialSurvey(loaded);
    expect(out.hiddenFields).toEqual([{ id: "userId", name: "User ID" }]);
  });

  it("prefers an already-camelCase hiddenFields when present", () => {
    const loaded = { ...SURVEY, hiddenFields: [{ id: "plan" }] };
    expect(normalizeInitialSurvey(loaded).hiddenFields).toEqual([{ id: "plan" }]);
  });

  it("defaults hiddenFields to [] when neither key is present", () => {
    expect(normalizeInitialSurvey(SURVEY).hiddenFields).toEqual([]);
  });
});

describe("normalizeInitialSurvey — snake→camel follow_ups bridge", () => {
  it("maps the serializer's snake_case follow_ups onto camelCase followUps", () => {
    // Regression: SurveySerializer emits `follow_ups` (snake); the builder edits `followUps`
    // (camel). Without this bridge a SAVED follow-up renders as "No follow-ups yet" on reload
    // and is wiped on the next Save.
    const loaded = {
      ...SURVEY,
      follow_ups: [{ id: "fu_1", trigger: { type: "responseFinished" } }],
    } as unknown as Survey;
    const out = normalizeInitialSurvey(loaded);
    expect(out.followUps).toEqual([{ id: "fu_1", trigger: { type: "responseFinished" } }]);
  });

  it("prefers an already-camelCase followUps when present", () => {
    const loaded = { ...SURVEY, followUps: [{ id: "fu_2" }] } as unknown as Survey;
    expect(normalizeInitialSurvey(loaded).followUps).toEqual([{ id: "fu_2" }]);
  });

  it("defaults followUps to [] when neither key is present", () => {
    expect(normalizeInitialSurvey(SURVEY).followUps).toEqual([]);
  });
});

describe("normalizeInitialSurvey — snake→camel welcome_card bridge", () => {
  it("maps the serializer's snake_case welcome_card onto camelCase welcomeCard", () => {
    // Regression: SurveySerializer emits `welcome_card` (snake); the builder's EndingsPanel
    // reads `welcomeCard` (camel). Without this bridge a configured welcome card renders empty
    // on reload.
    const loaded = {
      ...SURVEY,
      welcome_card: { enabled: true, headline: { default: "Welcome!" } },
    } as unknown as Survey;
    const out = normalizeInitialSurvey(loaded);
    expect(out.welcomeCard).toEqual({ enabled: true, headline: { default: "Welcome!" } });
  });

  it("prefers an already-camelCase welcomeCard when present", () => {
    const loaded = { ...SURVEY, welcomeCard: { enabled: true } } as unknown as Survey;
    expect(normalizeInitialSurvey(loaded).welcomeCard).toEqual({ enabled: true });
  });

  it("defaults welcomeCard to null when neither key is present", () => {
    expect(normalizeInitialSurvey(SURVEY).welcomeCard).toBeNull();
  });
});
