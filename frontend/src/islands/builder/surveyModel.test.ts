// ============================================================================
// Builder surveyModel — pure transform tests.
//
// Asserts the factories emit schema-valid defaults, the i18n default-key helpers
// preserve other languages, the immutable list ops behave, and the PUT envelope
// is shaped per openapi SurveyWrite.
// ============================================================================
import { describe, expect, it } from "vitest";

import { validateSurvey } from "../../lib/logic";
import type { Ending, Question, Survey } from "../../lib/types";
import {
  addChoice,
  addEnding,
  addHiddenField,
  addLanguage,
  addQuestion,
  cleanCloseConditions,
  disableWelcomeCard,
  enableWelcomeCard,
  isValidHiddenFieldId,
  moveEndingDown,
  moveEndingUp,
  newEndingId,
  removeEnding,
  removeHiddenField,
  cleanStyling,
  deleteChoice,
  deleteQuestion,
  getDefaultText,
  getLangText,
  makeQuestion,
  moveQuestionDown,
  moveQuestionUp,
  newChoiceId,
  newQuestionId,
  patchCloseConditions,
  patchStyling,
  removeLanguage,
  cleanDisplayOptions,
  patchDisplayOptions,
  setCaptureIp,
  setDefaultLanguage,
  setDefaultText,
  setSurveyType,
  setTriggers,
  toggleTrigger,
  setEndingText,
  setEndingType,
  setEndingUrl,
  setLangText,
  setSingleUse,
  setOptionalDefaultText,
  setOptionalLangText,
  setWelcomeButtonLabel,
  setWelcomeHeadline,
  setWelcomeHtml,
  toSurveyWrite,
  updateChoiceLabel,
  updateEnding,
  updateQuestion,
} from "./surveyModel";
import { PALETTE } from "./QuestionPalette";

const QUESTION_ID_RE = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;
const CHOICE_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/;

function baseSurvey(questions: Question[] = []): Survey {
  return { name: "S", questions, languages: [{ code: "en", default: true }] };
}

describe("id generation", () => {
  it("question ids match the schema pattern and avoid collisions", () => {
    const id = newQuestionId(["q_aaaaaa"]);
    expect(id).toMatch(QUESTION_ID_RE);
    expect(id).not.toBe("q_aaaaaa");
  });

  it("choice ids match the schema pattern", () => {
    expect(newChoiceId([])).toMatch(CHOICE_ID_RE);
  });
});

describe("makeQuestion — schema-valid defaults for every core type", () => {
  for (const entry of PALETTE) {
    it(`${entry.type} produces a question that passes validateSurvey`, () => {
      const q = makeQuestion(entry.type, []);
      expect(q.id).toMatch(QUESTION_ID_RE);
      expect(q.type).toBe(entry.type);
      expect(getDefaultText(q.headline)).not.toBe("");
      // The logic-level validator must accept it (deep config validation is server-side).
      const result = validateSurvey(baseSurvey([q]));
      expect(result.ok).toBe(true);
    });
  }

  it("singleSelect/multiSelect seed at least 2 choices", () => {
    for (const type of ["singleSelect", "multiSelect"] as const) {
      const q = makeQuestion(type, []);
      expect(Array.isArray(q.config.choices)).toBe(true);
      expect(q.config.choices.length).toBeGreaterThanOrEqual(2);
      const ids = q.config.choices.map((c: { id: string }) => c.id);
      ids.forEach((id: string) => expect(id).toMatch(CHOICE_ID_RE));
    }
  });

  it("rating seeds range within 3..10 and a scale", () => {
    const q = makeQuestion("rating", []);
    expect(q.config.range).toBeGreaterThanOrEqual(3);
    expect(q.config.range).toBeLessThanOrEqual(10);
    expect(["star", "number", "smiley"]).toContain(q.config.scale);
  });

  it("consent seeds a label with a default key", () => {
    const q = makeQuestion("consent", []);
    expect(q.config.label.default).toBeTruthy();
  });
});

describe("i18n default-key helpers preserve other languages", () => {
  it("setDefaultText keeps existing language keys", () => {
    const next = setDefaultText({ default: "Hi", fr: "Salut" }, "Hello");
    expect(next).toEqual({ default: "Hello", fr: "Salut" });
  });

  it("setOptionalDefaultText drops the default key on blank but keeps others", () => {
    expect(setOptionalDefaultText({ default: "x", de: "y" }, "")).toEqual({ de: "y" });
  });

  it("setOptionalDefaultText returns undefined when the map empties", () => {
    expect(setOptionalDefaultText({ default: "x" }, "")).toBeUndefined();
    expect(setOptionalDefaultText(undefined, "")).toBeUndefined();
  });
});

describe("getLangText — reads the right key for the active language", () => {
  it("default language reads the `default` key", () => {
    expect(getLangText({ default: "Hi", fr: "Bonjour" }, "en", "en")).toBe("Hi");
    // The literal "default" code also addresses the `default` key.
    expect(getLangText({ default: "Hi", fr: "Bonjour" }, "default", "default")).toBe("Hi");
  });

  it("non-default language reads its own key (raw, not the fallback)", () => {
    expect(getLangText({ default: "Hi", fr: "Bonjour" }, "fr", "en")).toBe("Bonjour");
    // Untranslated → empty box (NOT the default fallback), so the author sees it's blank.
    expect(getLangText({ default: "Hi" }, "fr", "en")).toBe("");
    expect(getLangText(undefined, "fr", "en")).toBe("");
  });
});

describe("setLangText — required i18n, per-language, default invariant", () => {
  it("default language writes the `default` key, preserving other languages", () => {
    expect(setLangText({ default: "Hi", fr: "Bonjour" }, "en", "Hello", "en")).toEqual({
      default: "Hello",
      fr: "Bonjour",
    });
  });

  it("non-default language writes its key, preserving `default`", () => {
    expect(setLangText({ default: "Hi" }, "fr", "Bonjour", "en")).toEqual({
      default: "Hi",
      fr: "Bonjour",
    });
  });

  it("blank non-default text drops ONLY that lang key, keeping `default`", () => {
    expect(setLangText({ default: "Hi", fr: "Bonjour" }, "fr", "", "en")).toEqual({
      default: "Hi",
    });
  });

  it("always retains a `default` key even when the source map lacked one", () => {
    const out = setLangText(undefined, "fr", "Bonjour", "en");
    expect(out.default).toBe("");
    expect(out.fr).toBe("Bonjour");
  });
});

describe("setOptionalLangText — optional i18n, per-language, default invariant", () => {
  it("default language defers to setOptionalDefaultText (drops default when blank)", () => {
    expect(setOptionalLangText({ default: "x", de: "y" }, "en", "", "en")).toEqual({ de: "y" });
    expect(setOptionalLangText({ default: "x" }, "en", "", "en")).toBeUndefined();
  });

  it("non-default blank drops ONLY that lang key, keeping `default`", () => {
    expect(setOptionalLangText({ default: "Hi", fr: "X" }, "fr", "", "en")).toEqual({
      default: "Hi",
    });
  });

  it("returns undefined only once the whole map empties", () => {
    expect(setOptionalLangText({ fr: "X" }, "fr", "", "en")).toBeUndefined();
  });

  it("non-default writes its key, preserving `default`", () => {
    expect(setOptionalLangText({ default: "Hi" }, "fr", "Bonjour", "en")).toEqual({
      default: "Hi",
      fr: "Bonjour",
    });
  });
});

describe("language management — CRUD with default-invariant guards", () => {
  const survey = (...langs: Survey["languages"]): Survey => ({
    name: "S",
    questions: [],
    languages: langs,
  });

  it("addLanguage appends a non-default, enabled entry with an optional label", () => {
    const out = addLanguage(survey({ code: "en", default: true }), "fr", "Français");
    expect(out.languages).toEqual([
      { code: "en", default: true },
      { code: "fr", default: false, label: "Français" },
    ]);
  });

  it("addLanguage no-ops (same reference) on a blank or duplicate code", () => {
    const s = survey({ code: "en", default: true });
    expect(addLanguage(s, "  ")).toBe(s);
    expect(addLanguage(s, "en")).toBe(s);
  });

  it("removeLanguage refuses the default and the last remaining language", () => {
    const oneDefault = survey({ code: "en", default: true });
    expect(removeLanguage(oneDefault, "en")).toBe(oneDefault); // is the default
    const twoLangs = survey({ code: "en", default: true }, { code: "fr", default: false });
    expect(removeLanguage(twoLangs, "en")).toBe(twoLangs); // still the default
    expect(removeLanguage(twoLangs, "fr").languages).toEqual([{ code: "en", default: true }]);
  });

  it("removeLanguage of an unknown code no-ops", () => {
    const s = survey({ code: "en", default: true }, { code: "fr", default: false });
    expect(removeLanguage(s, "de")).toBe(s);
  });

  it("setDefaultLanguage flips exactly one default; old default goes false", () => {
    const s = survey({ code: "en", default: true }, { code: "fr", default: false });
    const out = setDefaultLanguage(s, "fr");
    expect(out.languages).toEqual([
      { code: "en", default: false },
      { code: "fr", default: true },
    ]);
    expect(out.languages.filter((l) => l.default)).toHaveLength(1);
  });

  it("setDefaultLanguage refuses an unknown code", () => {
    const s = survey({ code: "en", default: true });
    expect(setDefaultLanguage(s, "fr")).toBe(s);
  });

  it("the list always retains exactly one default after any allowed op", () => {
    let s = survey({ code: "en", default: true });
    s = addLanguage(s, "fr", "Français");
    s = addLanguage(s, "de");
    s = setDefaultLanguage(s, "de");
    s = removeLanguage(s, "fr");
    const defaults = s.languages.filter((l) => l.default);
    expect(defaults).toHaveLength(1);
    expect(defaults[0].code).toBe("de");
  });
});

describe("immutable list ops", () => {
  const q1 = makeQuestion("openText", []);
  const q2 = makeQuestion("rating", [q1.id]);
  const q3 = makeQuestion("nps", [q1.id, q2.id]);

  it("addQuestion appends without mutating the input", () => {
    const s = baseSurvey([q1]);
    const out = addQuestion(s, q2);
    expect(out.questions.map((q) => q.id)).toEqual([q1.id, q2.id]);
    expect(s.questions).toHaveLength(1); // original untouched
  });

  it("deleteQuestion removes by id", () => {
    const out = deleteQuestion(baseSurvey([q1, q2]), q1.id);
    expect(out.questions.map((q) => q.id)).toEqual([q2.id]);
  });

  it("moveQuestionUp / Down reorder; no-op at the boundaries", () => {
    const s = baseSurvey([q1, q2, q3]);
    expect(moveQuestionDown(s, 0).questions.map((q) => q.id)).toEqual([q2.id, q1.id, q3.id]);
    expect(moveQuestionUp(s, 2).questions.map((q) => q.id)).toEqual([q1.id, q3.id, q2.id]);
    expect(moveQuestionUp(s, 0).questions.map((q) => q.id)).toEqual([q1.id, q2.id, q3.id]); // top no-op
    expect(moveQuestionDown(s, 2).questions.map((q) => q.id)).toEqual([q1.id, q2.id, q3.id]); // bottom no-op
  });

  it("updateQuestion replaces by id", () => {
    const edited = { ...q1, headline: setDefaultText(q1.headline, "Changed") };
    const out = updateQuestion(baseSurvey([q1, q2]), edited);
    expect(getDefaultText(out.questions[0].headline)).toBe("Changed");
  });
});

describe("choice transforms", () => {
  it("addChoice appends a labelled choice with a valid id", () => {
    const q = makeQuestion("singleSelect", []);
    const before = q.config.choices.length;
    const out = addChoice(q, "Extra");
    expect(out.config.choices).toHaveLength(before + 1);
    const last = out.config.choices[out.config.choices.length - 1];
    expect(last.id).toMatch(CHOICE_ID_RE);
    expect(last.label.default).toBe("Extra");
  });

  it("updateChoiceLabel edits the default key in place", () => {
    const q = makeQuestion("singleSelect", []);
    const cid = q.config.choices[0].id;
    const out = updateChoiceLabel(q, cid, "Renamed");
    expect(out.config.choices[0].label.default).toBe("Renamed");
  });

  it("deleteChoice removes by id", () => {
    const q = makeQuestion("multiSelect", []);
    const cid = q.config.choices[0].id;
    const out = deleteChoice(q, cid);
    expect(out.config.choices.find((c: { id: string }) => c.id === cid)).toBeUndefined();
  });
});

describe("toSurveyWrite — openapi SurveyWrite envelope", () => {
  it("includes the required keys and forwards environment_id when present", () => {
    const s: Survey = {
      ...baseSurvey([makeQuestion("openText", [])]),
      name: "My survey",
      styling: { brandColor: "#000" },
    };
    (s as unknown as Record<string, unknown>).environment_id = "env-123";
    const body = toSurveyWrite(s);
    expect(body.name).toBe("My survey");
    expect(Array.isArray(body.questions)).toBe(true);
    expect(Array.isArray(body.languages)).toBe(true);
    expect(body.styling).toEqual({ brandColor: "#000" });
    expect(body.environment_id).toBe("env-123");
  });

  it("omits environment_id when the survey has none", () => {
    const body = toSurveyWrite(baseSurvey([]));
    expect("environment_id" in body).toBe(false);
    expect(body.name).toBe("S");
  });

  it("round-trips multi-language i18n strings (default + fr keys survive)", () => {
    // Build a question whose headline + a choice label are translated into `fr`
    // (on top of the required `default` key) and confirm the writer passes them
    // through opaquely — no key is dropped or default-collapsed.
    let q = makeQuestion("singleSelect", []);
    const cid = q.config.choices[0].id;
    // Default-language edits write `default`; fr edits add the fr key alongside.
    q = { ...q, headline: setLangText(q.headline, "fr", "Bonjour", "en") };
    q = updateChoiceLabel(q, cid, "Première option", "fr", "en");

    const survey: Survey = {
      name: "Multilang",
      questions: [q],
      languages: [
        { code: "en", default: true },
        { code: "fr", default: false, label: "Français" },
      ],
    };
    const body = toSurveyWrite(survey);

    expect(body.languages).toEqual(survey.languages);
    const outQ = (body.questions as Question[])[0];
    expect(outQ.headline).toEqual({ default: "Pick one", fr: "Bonjour" });
    expect(outQ.config.choices[0].label).toEqual({ default: "Option 1", fr: "Première option" });
  });
});

describe("cleanStyling", () => {
  it("drops blank color strings (omit-when-default)", () => {
    expect(cleanStyling({ brandColor: "", questionColor: "  ", buttonColor: "#fff" })).toEqual({
      buttonColor: "#fff",
    });
  });

  it("clamps roundness into [0, 32] and drops non-numeric", () => {
    expect(cleanStyling({ roundness: 50 }).roundness).toBe(32);
    expect(cleanStyling({ roundness: -5 }).roundness).toBe(0);
    expect(cleanStyling({ roundness: 0 }).roundness).toBe(0);
    expect(cleanStyling({ roundness: NaN }).roundness).toBeUndefined();
  });

  it("keeps hideProgressBar only when true, cardArrangement only when casual", () => {
    expect(cleanStyling({ hideProgressBar: false })).toEqual({});
    expect(cleanStyling({ hideProgressBar: true })).toEqual({ hideProgressBar: true });
    expect(cleanStyling({ cardArrangement: "straight" })).toEqual({});
    expect(cleanStyling({ cardArrangement: "casual" })).toEqual({ cardArrangement: "casual" });
  });

  it("keeps background only for color/image WITH a value; 'none'/blank drops the key", () => {
    expect(cleanStyling({ background: { type: "none" } })).toEqual({});
    expect(cleanStyling({ background: { type: "color", value: "" } })).toEqual({});
    expect(cleanStyling({ background: { type: "color", value: "#101010" } })).toEqual({
      background: { type: "color", value: "#101010" },
    });
    expect(cleanStyling({ background: { type: "image", value: "  https://x/bg.jpg  " } })).toEqual({
      background: { type: "image", value: "https://x/bg.jpg" },
    });
  });

  it("trims logoUrl and drops it when blank", () => {
    expect(cleanStyling({ logoUrl: "   " })).toEqual({});
    expect(cleanStyling({ logoUrl: " https://x/logo.png " })).toEqual({
      logoUrl: "https://x/logo.png",
    });
  });
});

describe("patchStyling", () => {
  it("merges a patch onto existing styling and returns a fresh Survey", () => {
    const s = { ...baseSurvey([]), styling: { brandColor: "#aaa" } };
    const next = patchStyling(s, { buttonColor: "#bbb" });
    expect(next).not.toBe(s);
    expect(next.styling).toEqual({ brandColor: "#aaa", buttonColor: "#bbb" });
  });

  it("clears a color back to default when the patch passes an empty string", () => {
    const s = { ...baseSurvey([]), styling: { brandColor: "#aaa", buttonColor: "#bbb" } };
    const next = patchStyling(s, { brandColor: "" });
    expect(next.styling).toEqual({ buttonColor: "#bbb" });
  });

  it("starts clean when the survey has no styling yet", () => {
    const next = patchStyling(baseSurvey([]), { brandColor: "#0d9488" });
    expect(next.styling).toEqual({ brandColor: "#0d9488" });
  });
});

describe("settings transforms (single_use / capture_ip)", () => {
  it("setSingleUse / setCaptureIp set the flag on a fresh Survey", () => {
    const s = baseSurvey([]);
    const a = setSingleUse(s, true);
    expect(a).not.toBe(s);
    expect(a.single_use).toBe(true);
    const b = setCaptureIp(s, true);
    expect(b.capture_ip).toBe(true);
  });
});

describe("cleanCloseConditions", () => {
  it("returns null when neither key is meaningfully set", () => {
    expect(cleanCloseConditions({})).toBeNull();
    expect(cleanCloseConditions({ by_date: "  " })).toBeNull();
    expect(cleanCloseConditions({ by_response_cap: 0 })).toBeNull(); // below schema min (1)
  });

  it("keeps a non-blank by_date and an integer cap >= 1", () => {
    expect(cleanCloseConditions({ by_date: "2026-06-01T00:00:00.000Z" })).toEqual({
      by_date: "2026-06-01T00:00:00.000Z",
    });
    expect(cleanCloseConditions({ by_response_cap: 50 })).toEqual({ by_response_cap: 50 });
    expect(cleanCloseConditions({ by_response_cap: 3.9 })).toEqual({ by_response_cap: 3 }); // floored
  });
});

describe("patchCloseConditions", () => {
  it("merges a patch and clears close_on to null when emptied", () => {
    const s = { ...baseSurvey([]), close_on: { by_response_cap: 100 } };
    const withDate = patchCloseConditions(s, { by_date: "2026-06-01T00:00:00.000Z" });
    expect(withDate.close_on).toEqual({
      by_response_cap: 100,
      by_date: "2026-06-01T00:00:00.000Z",
    });
    // Clearing both keys → null (no auto-close).
    const cleared = patchCloseConditions(
      patchCloseConditions(s, { by_response_cap: undefined }),
      { by_date: "" },
    );
    expect(cleared.close_on).toBeNull();
  });
});

describe("toSurveyWrite — settings fields", () => {
  it("forwards capture_ip and close_on (the silent-drop fix)", () => {
    const s: Survey = {
      ...baseSurvey([]),
      capture_ip: true,
      close_on: { by_response_cap: 25 },
    };
    const body = toSurveyWrite(s);
    expect(body.capture_ip).toBe(true);
    expect(body.close_on).toEqual({ by_response_cap: 25 });
  });

  it("omits capture_ip/close_on when the survey has neither", () => {
    const body = toSurveyWrite(baseSurvey([]));
    expect("capture_ip" in body).toBe(false);
    expect("close_on" in body).toBe(false);
  });

  it("forwards hiddenFields under the snake_case hidden_fields body key", () => {
    const s: Survey = {
      ...baseSurvey([]),
      hiddenFields: [{ id: "userId", name: "User ID" }, { id: "plan" }],
    };
    const body = toSurveyWrite(s);
    expect(body.hidden_fields).toEqual([{ id: "userId", name: "User ID" }, { id: "plan" }]);
  });

  it("omits hidden_fields when the survey has no hiddenFields", () => {
    const body = toSurveyWrite(baseSurvey([]));
    expect("hidden_fields" in body).toBe(false);
  });
});

describe("hidden fields — addHiddenField / removeHiddenField / isValidHiddenFieldId", () => {
  function q(id: string): Question {
    return { id, type: "openText", headline: { default: id }, config: {} };
  }

  it("validates hidden-field ids against the question-id charset", () => {
    expect(isValidHiddenFieldId("userId")).toBe(true);
    expect(isValidHiddenFieldId("a_b-c")).toBe(true);
    expect(isValidHiddenFieldId("1bad")).toBe(false); // can't start with a digit
    expect(isValidHiddenFieldId("has space")).toBe(false);
    expect(isValidHiddenFieldId("")).toBe(false);
  });

  it("adds a hidden field with id + optional name (fresh survey)", () => {
    const s = baseSurvey([]);
    const next = addHiddenField(s, "userId", "User ID");
    expect(next).not.toBe(s); // fresh object → dirty-tracking fires
    expect(next.hiddenFields).toEqual([{ id: "userId", name: "User ID" }]);
    // name is optional.
    const next2 = addHiddenField(next, "plan");
    expect(next2.hiddenFields).toEqual([{ id: "userId", name: "User ID" }, { id: "plan" }]);
  });

  it("no-ops (same survey) on a blank / invalid / duplicate id", () => {
    const s = addHiddenField(baseSurvey([]), "userId");
    expect(addHiddenField(s, "")).toBe(s); // blank
    expect(addHiddenField(s, "1bad")).toBe(s); // invalid charset
    expect(addHiddenField(s, "userId")).toBe(s); // duplicate
  });

  it("no-ops on a collision with a question or ending id", () => {
    const s: Survey = {
      ...baseSurvey([q("q1")]),
      endings: [{ id: "end1", type: "endScreen" }],
    };
    expect(addHiddenField(s, "q1")).toBe(s); // collides with a question id
    expect(addHiddenField(s, "end1")).toBe(s); // collides with an ending id
    expect(addHiddenField(s, "fresh").hiddenFields).toEqual([{ id: "fresh" }]);
  });

  it("removes a hidden field by id; no-ops when not present", () => {
    const s = addHiddenField(addHiddenField(baseSurvey([]), "a"), "b");
    const removed = removeHiddenField(s, "a");
    expect(removed.hiddenFields).toEqual([{ id: "b" }]);
    expect(removeHiddenField(s, "nope")).toBe(s); // no-op (same survey)
  });
});

// =========================================================================== //
//  welcome card — enable / disable / per-language edits                       //
// =========================================================================== //
const QUESTION_ID_RE2 = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;

/** A multi-language base survey (en default + fr) for i18n-preservation assertions. */
function multiLangSurvey(questions: Question[] = []): Survey {
  return {
    name: "S",
    questions,
    languages: [
      { code: "en", default: true },
      { code: "fr", default: false },
    ],
  };
}

function q(id: string): Question {
  return { id, type: "openText", headline: { default: id }, config: {} };
}

describe("welcome card — enable / disable / edits", () => {
  it("untouched survey stays byte-identical (no welcomeCard key, disable is a no-op)", () => {
    const s = baseSurvey([q("q1")]);
    expect("welcomeCard" in s).toBe(false);
    // Disabling a survey that never had a welcome card returns the SAME reference
    // (dirty-tracking must not trip for an untouched no-welcome survey).
    expect(disableWelcomeCard(s)).toBe(s);
  });

  it("enabling seeds a schema-valid card with the required headline default key", () => {
    const s = baseSurvey([q("q1")]);
    const next = enableWelcomeCard(s);
    expect(next).not.toBe(s);
    expect(next.welcomeCard).toEqual({ enabled: true, headline: { default: "Welcome" } });
    // re-enabling is a no-op (already enabled).
    expect(enableWelcomeCard(next)).toBe(next);
  });

  it("disabling an enabled card writes welcomeCard:null (current no-welcome shape)", () => {
    const s = enableWelcomeCard(baseSurvey([q("q1")]));
    const off = disableWelcomeCard(s);
    expect(off).not.toBe(s);
    expect(off.welcomeCard).toBeNull();
    // a null card is idempotent under disable (still null, same reference).
    expect(disableWelcomeCard(off)).toBe(off);
  });

  it("edits headline/html/buttonLabel; default-lang writes the default key", () => {
    let s = enableWelcomeCard(baseSurvey([q("q1")]));
    s = setWelcomeHeadline(s, "Hi there");
    s = setWelcomeHtml(s, "<p>Body</p>");
    s = setWelcomeButtonLabel(s, "Begin");
    expect(s.welcomeCard).toEqual({
      enabled: true,
      headline: { default: "Hi there" },
      html: { default: "<p>Body</p>" },
      buttonLabel: { default: "Begin" },
    });
  });

  it("edits no-op when the card isn't enabled", () => {
    const s = baseSurvey([q("q1")]);
    expect(setWelcomeHeadline(s, "x")).toBe(s);
    expect(setWelcomeHtml(s, "x")).toBe(s);
    expect(setWelcomeButtonLabel(s, "x")).toBe(s);
  });

  it("multi-language edits preserve the default key while adding a non-default lang", () => {
    let s = enableWelcomeCard(multiLangSurvey([q("q1")]));
    s = setWelcomeHeadline(s, "Welcome", "en", "en"); // default lang -> default key
    s = setWelcomeHeadline(s, "Bienvenue", "fr", "en"); // non-default -> fr key
    s = setWelcomeHtml(s, "<p>Hello</p>", "en", "en");
    s = setWelcomeHtml(s, "<p>Bonjour</p>", "fr", "en");
    expect(s.welcomeCard?.headline).toEqual({ default: "Welcome", fr: "Bienvenue" });
    expect(s.welcomeCard?.html).toEqual({ default: "<p>Hello</p>", fr: "<p>Bonjour</p>" });
    // blanking the fr html drops ONLY the fr key, default is preserved.
    s = setWelcomeHtml(s, "", "fr", "en");
    expect(s.welcomeCard?.html).toEqual({ default: "<p>Hello</p>" });
  });

  it("a survey with an enabled welcome card validates (logic engine)", () => {
    let s = enableWelcomeCard(baseSurvey([q("q1")]));
    s = setWelcomeHeadline(s, "Welcome");
    expect(validateSurvey(s)).toEqual({ ok: true });
  });
});

// =========================================================================== //
//  endings — add / update / type-switch / text / url / remove / reorder       //
// =========================================================================== //
describe("endings — id generation + namespace uniqueness", () => {
  it("generated ids match the schema pattern and avoid the shared namespace", () => {
    const s: Survey = {
      ...baseSurvey([q("q1")]),
      endings: [{ id: "e_existing", type: "endScreen" }],
      hiddenFields: [{ id: "userId" }],
    };
    const id = newEndingId(s);
    expect(id).toMatch(QUESTION_ID_RE2);
    expect(["q1", "e_existing", "userId"]).not.toContain(id);
  });

  it("adding an ending never collides with a question / hidden-field id", () => {
    let s: Survey = { ...baseSurvey([q("q1")]), hiddenFields: [{ id: "h1" }] };
    s = addEnding(s, "endScreen");
    s = addEnding(s, "redirect");
    const ids = (s.endings ?? []).map((e) => e.id);
    expect(new Set(ids).size).toBe(2); // unique
    expect(ids).not.toContain("q1");
    expect(ids).not.toContain("h1");
    // and the whole survey passes the shared-namespace uniqueness check.
    expect(validateSurvey(s)).toEqual({ ok: true });
  });
});

describe("endings — add / shapes", () => {
  it("adds a minimal endScreen ({id,type}) — runtime falls back to default copy", () => {
    const s = addEnding(baseSurvey([q("q1")]), "endScreen");
    expect(s.endings).toHaveLength(1);
    const e = s.endings![0];
    expect(e.type).toBe("endScreen");
    expect(Object.keys(e).sort()).toEqual(["id", "type"]);
  });

  it("adds a redirect with the schema-required (empty) redirectUrl key", () => {
    const s = addEnding(baseSurvey([q("q1")]), "redirect");
    const e = s.endings![0];
    expect(e.type).toBe("redirect");
    expect(e.redirectUrl).toBe("");
  });

  it("appends, keeping a fresh survey reference each time", () => {
    const s0 = baseSurvey([q("q1")]);
    const s1 = addEnding(s0, "endScreen");
    const s2 = addEnding(s1, "endScreen");
    expect(s1).not.toBe(s0);
    expect(s2.endings).toHaveLength(2);
  });
});

describe("endings — update / type-switch / text / url", () => {
  function withEnding(type: Ending["type"] = "endScreen"): { s: Survey; id: string } {
    const s = addEnding(baseSurvey([q("q1")]), type);
    return { s, id: s.endings![0].id };
  }

  it("updateEnding replaces by id; no-ops on an unknown id", () => {
    const { s, id } = withEnding();
    const updated = updateEnding(s, { id, type: "endScreen", headline: { default: "Bye" } });
    expect(updated.endings![0].headline).toEqual({ default: "Bye" });
    // unknown id -> same survey.
    expect(updateEnding(s, { id: "nope", type: "endScreen" })).toBe(s);
  });

  it("setEndingType switches endScreen -> redirect (seeds redirectUrl) and keeps the id", () => {
    const { s, id } = withEnding("endScreen");
    const r = setEndingType(s, id, "redirect");
    expect(r.endings![0].id).toBe(id); // id stable
    expect(r.endings![0].type).toBe("redirect");
    expect(r.endings![0].redirectUrl).toBe("");
    // same type -> no-op.
    expect(setEndingType(r, id, "redirect")).toBe(r);
  });

  it("setEndingType redirect -> endScreen drops redirectUrl, keeps shared content", () => {
    let { s, id } = withEnding("redirect");
    s = setEndingUrl(s, id, "redirectUrl", "https://example.com");
    s = setEndingText(s, id, "headline", "Thanks");
    const back = setEndingType(s, id, "endScreen");
    expect("redirectUrl" in back.endings![0]).toBe(false);
    expect(back.endings![0].headline).toEqual({ default: "Thanks" }); // content survives
  });

  it("setEndingText edits the optional i18n fields per language; blank drops the lang key", () => {
    const base = addEnding(multiLangSurvey([q("q1")]), "endScreen");
    const id = base.endings![0].id;
    let s = setEndingText(base, id, "headline", "Thank you", "en", "en");
    s = setEndingText(s, id, "headline", "Merci", "fr", "en");
    s = setEndingText(s, id, "subheader", "We appreciate it", "en", "en");
    expect(s.endings![0].headline).toEqual({ default: "Thank you", fr: "Merci" });
    expect(s.endings![0].subheader).toEqual({ default: "We appreciate it" });
    // blanking the only key removes the whole field (no empty optional map persists).
    s = setEndingText(s, id, "subheader", "", "en", "en");
    expect("subheader" in s.endings![0]).toBe(false);
    // blanking the fr headline drops only fr; default survives.
    s = setEndingText(s, id, "headline", "", "fr", "en");
    expect(s.endings![0].headline).toEqual({ default: "Thank you" });
  });

  it("setEndingUrl: buttonLink drops on blank, redirectUrl keeps the empty string", () => {
    let { s, id } = withEnding("redirect");
    s = setEndingUrl(s, id, "buttonLink", "https://x.test");
    expect(s.endings![0].buttonLink).toBe("https://x.test");
    s = setEndingUrl(s, id, "buttonLink", "  ");
    expect("buttonLink" in s.endings![0]).toBe(false); // blank drops it
    s = setEndingUrl(s, id, "redirectUrl", "https://r.test");
    expect(s.endings![0].redirectUrl).toBe("https://r.test");
    s = setEndingUrl(s, id, "redirectUrl", "");
    expect(s.endings![0].redirectUrl).toBe(""); // required key kept
  });

  it("text/url/type edits no-op on an unknown id", () => {
    const { s } = withEnding();
    expect(setEndingText(s, "nope", "headline", "x")).toBe(s);
    expect(setEndingUrl(s, "nope", "redirectUrl", "x")).toBe(s);
    expect(setEndingType(s, "nope", "redirect")).toBe(s);
  });
});

describe("endings — remove / reorder (id-stable)", () => {
  function threeEndings(): Survey {
    let s = baseSurvey([q("q1")]);
    s = addEnding(s, "endScreen");
    s = addEnding(s, "endScreen");
    s = addEnding(s, "redirect");
    return s;
  }

  it("removeEnding drops by id; no-ops on an unknown id", () => {
    const s = threeEndings();
    const id = s.endings![1].id;
    const removed = removeEnding(s, id);
    expect(removed.endings).toHaveLength(2);
    expect(removed.endings!.map((e) => e.id)).not.toContain(id);
    expect(removeEnding(s, "nope")).toBe(s);
  });

  it("reorder swaps neighbours and keeps ids stable (logic targets survive)", () => {
    const s = threeEndings();
    const ids = s.endings!.map((e) => e.id);
    const up = moveEndingUp(s, 2); // swap idx 1 and 2
    expect(up.endings!.map((e) => e.id)).toEqual([ids[0], ids[2], ids[1]]);
    const down = moveEndingDown(s, 0); // swap idx 0 and 1
    expect(down.endings!.map((e) => e.id)).toEqual([ids[1], ids[0], ids[2]]);
    // out-of-range moves no-op (same survey).
    expect(moveEndingUp(s, 0)).toBe(s);
    expect(moveEndingDown(s, 2)).toBe(s);
  });
});

describe("endings — logic id-stability protects existing references", () => {
  // A survey where q1's logic ends the survey on a specific ending id.
  function surveyWithEndSurveyLogic(): { survey: Survey; endingId: string } {
    let survey = baseSurvey([q("q1")]);
    survey = addEnding(survey, "endScreen");
    const endingId = survey.endings![0].id;
    survey.questions[0].logic = [
      {
        id: "r1",
        condition: { leftQuestionId: "q1", operator: "isSubmitted" },
        action: { type: "endSurvey", endingId },
      },
    ];
    return { survey, endingId };
  }

  it("logic targeting an ending validates while the ending exists", () => {
    const { survey } = surveyWithEndSurveyLogic();
    expect(validateSurvey(survey)).toEqual({ ok: true });
  });

  it("editing the ending content keeps the id, so logic still resolves", () => {
    const { survey, endingId } = surveyWithEndSurveyLogic();
    const edited = setEndingText(survey, endingId, "headline", "Thanks!");
    expect(edited.endings![0].id).toBe(endingId); // id unchanged
    expect(validateSurvey(edited)).toEqual({ ok: true });
  });

  it("removing a referenced ending surfaces unknown_target (not a silent break)", () => {
    const { survey, endingId } = surveyWithEndSurveyLogic();
    const removed = removeEnding(survey, endingId);
    const result = validateSurvey(removed);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.some((e) => e.code === "unknown_target")).toBe(true);
    }
  });
});

// =========================================================================== //
//  toSurveyWrite — welcome_card + endings round-trip                          //
// =========================================================================== //
describe("toSurveyWrite — welcome_card + endings", () => {
  it("forwards welcome_card under the snake_case key when set", () => {
    let s = enableWelcomeCard(baseSurvey([q("q1")]));
    s = setWelcomeHeadline(s, "Welcome");
    const body = toSurveyWrite(s);
    expect(body.welcome_card).toEqual({ enabled: true, headline: { default: "Welcome" } });
  });

  it("forwards welcome_card: null when the card is disabled", () => {
    const s = disableWelcomeCard(enableWelcomeCard(baseSurvey([q("q1")])));
    const body = toSurveyWrite(s);
    expect(body.welcome_card).toBeNull();
  });

  it("omits welcome_card entirely when never touched (single-language flow unchanged)", () => {
    const body = toSurveyWrite(baseSurvey([q("q1")]));
    expect("welcome_card" in body).toBe(false);
  });

  it("round-trips endings exactly (id/type/content)", () => {
    let s = baseSurvey([q("q1")]);
    s = addEnding(s, "endScreen");
    const id = s.endings![0].id;
    s = setEndingText(s, id, "headline", "Thanks");
    s = addEnding(s, "redirect");
    const rid = s.endings![1].id;
    s = setEndingUrl(s, rid, "redirectUrl", "https://example.com/done");
    const body = toSurveyWrite(s);
    expect(body.endings).toEqual([
      { id, type: "endScreen", headline: { default: "Thanks" } },
      { id: rid, type: "redirect", redirectUrl: "https://example.com/done" },
    ]);
  });

  it("omits endings entirely when never touched", () => {
    const body = toSurveyWrite(baseSurvey([q("q1")]));
    expect("endings" in body).toBe(false);
  });
});

// --------------------------------------------------------------------------- //
//  app-survey settings (survey_type / triggers / display_options — WS-11)      //
// --------------------------------------------------------------------------- //
describe("app-survey settings transforms", () => {
  it("setSurveyType returns a FRESH survey with the channel set", () => {
    const s = baseSurvey([q("q1")]);
    const app = setSurveyType(s, "app");
    expect(app).not.toBe(s); // fresh object → dirty-tracking fires
    expect(app.survey_type).toBe("app");
    const link = setSurveyType(app, "link");
    expect(link.survey_type).toBe("link");
  });

  it("setTriggers replaces the list with a fresh copy", () => {
    const s = baseSurvey([q("q1")]);
    const next = setTriggers(s, ["a1", "a2"]);
    expect(next).not.toBe(s);
    expect(next.triggers).toEqual(["a1", "a2"]);
    // setting [] clears.
    expect(setTriggers(next, []).triggers).toEqual([]);
  });

  it("toggleTrigger adds when absent and removes when present", () => {
    let s = baseSurvey([q("q1")]);
    s = toggleTrigger(s, "a1");
    expect(s.triggers).toEqual(["a1"]);
    s = toggleTrigger(s, "a2");
    expect(s.triggers).toEqual(["a1", "a2"]);
    s = toggleTrigger(s, "a1"); // remove
    expect(s.triggers).toEqual(["a2"]);
  });

  it("cleanDisplayOptions keeps valid keys + clamps percentage; drops invalid", () => {
    expect(cleanDisplayOptions({ displayLimit: 3, displayPercentage: 50 })).toEqual({
      displayLimit: 3,
      displayPercentage: 50,
    });
    // sub-1 limit dropped; percentage clamped to [0, 100].
    expect(cleanDisplayOptions({ displayLimit: 0, displayPercentage: 150 })).toEqual({
      displayPercentage: 100,
    });
    expect(cleanDisplayOptions({ displayPercentage: -5 })).toEqual({ displayPercentage: 0 });
    // floors non-integer limit; NaN drops the key entirely.
    expect(cleanDisplayOptions({ displayLimit: 4.9 })).toEqual({ displayLimit: 4 });
    expect(cleanDisplayOptions({})).toEqual({});
  });

  it("patchDisplayOptions merges + cleans onto a fresh survey", () => {
    let s = baseSurvey([q("q1")]);
    s = patchDisplayOptions(s, { displayLimit: 2 });
    expect(s.display_options).toEqual({ displayLimit: 2 });
    s = patchDisplayOptions(s, { displayPercentage: 30 });
    expect(s.display_options).toEqual({ displayLimit: 2, displayPercentage: 30 });
    // clearing a key via undefined drops it.
    s = patchDisplayOptions(s, { displayLimit: undefined });
    expect(s.display_options).toEqual({ displayPercentage: 30 });
  });
});

describe("toSurveyWrite — app-survey fields", () => {
  it("a link survey NEVER carries survey_type/triggers/display_options (byte-identical wire)", () => {
    const body = toSurveyWrite(baseSurvey([q("q1")]));
    expect("survey_type" in body).toBe(false);
    expect("triggers" in body).toBe(false);
    expect("display_options" in body).toBe(false);
  });

  it("carries survey_type/triggers/display_options once set", () => {
    let s = baseSurvey([q("q1")]);
    s = setSurveyType(s, "app");
    s = setTriggers(s, ["a1", "a2"]);
    s = patchDisplayOptions(s, { displayLimit: 5, displayPercentage: 80 });
    const body = toSurveyWrite(s);
    expect(body.survey_type).toBe("app");
    expect(body.triggers).toEqual(["a1", "a2"]);
    expect(body.display_options).toEqual({ displayLimit: 5, displayPercentage: 80 });
  });

  it("a survey explicitly set back to link still carries survey_type=link (intentional switch)", () => {
    let s = baseSurvey([q("q1")]);
    s = setSurveyType(s, "app");
    s = setSurveyType(s, "link");
    const body = toSurveyWrite(s);
    expect(body.survey_type).toBe("link");
  });
});
