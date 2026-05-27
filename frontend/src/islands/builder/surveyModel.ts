// ============================================================================
// OpenSurvey — Builder survey-model helpers (pure, no React).
//
// Immutable transforms over the SHARED Survey definition (../../lib/types, the
// frozen contracts/survey-schema.json shape). Every "add question" factory emits
// a record that already satisfies the schema's per-type `oneOf` (required config
// keys, minItems on choices, rating range bounds, i18nString `default` key) so a
// freshly-added question never trips server-side validateSurvey.
//
// i18n scope: the editor writes a SPECIFIC language key. When the active authoring
// language is the survey default (or the literal "default"), it writes the reserved
// `default` i18nString key — byte-identical to the single-language flow. When the
// active language is a NON-default one (e.g. "fr"), it writes that lang key while
// PRESERVING `default` and all other language keys. The lang-aware writers below
// (setLangText / setOptionalLangText) generalize the older default-only helpers,
// which remain for callers that don't manage languages.
// ============================================================================
import type {
  Block,
  BlockRandomize,
  Choice,
  CloseConditions,
  ConditionGroup,
  Ending,
  FollowUp,
  FollowUpRecipient,
  HiddenField,
  I18nString,
  I18nStringOptional,
  Language,
  PictureChoiceOption,
  Question,
  Quota,
  Styling,
  Survey,
  Variable,
  WelcomeCard,
} from "../../lib/types";
import type { CoreQuestionType } from "./types";

// --------------------------------------------------------------------------- //
//  id generation (schema-valid)                                               //
// --------------------------------------------------------------------------- //
/** Random url-safe-ish suffix (lowercase alnum) used to make ids unique. */
function shortId(len = 6): string {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < len; i++) {
    out += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return out;
}

/**
 * A new question id matching survey-schema.json #/$defs/questionId
 * (`^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`), guaranteed not to collide with `existing`.
 */
export function newQuestionId(existing: Iterable<string>): string {
  const taken = new Set(existing);
  let id = `q_${shortId()}`;
  while (taken.has(id)) id = `q_${shortId()}`;
  return id;
}

/**
 * A new choice id matching survey-schema.json #/$defs/choiceId
 * (`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`), unique within `existing`.
 */
export function newChoiceId(existing: Iterable<string>): string {
  const taken = new Set(existing);
  let id = `c_${shortId()}`;
  while (taken.has(id)) id = `c_${shortId()}`;
  return id;
}

// --------------------------------------------------------------------------- //
//  i18n key helpers (default-key + per-language writers)                       //
// --------------------------------------------------------------------------- //
/** Read the `default` text from a (possibly undefined) i18n map. */
export function getDefaultText(str: I18nString | I18nStringOptional | undefined): string {
  if (!str) return "";
  return typeof str.default === "string" ? str.default : "";
}

/**
 * True when `lang` addresses the i18nString `default` key — i.e. the survey's
 * default-language code, or the literal reserved "default". In both modes a write
 * targets the `default` key (preserving the single-language flow byte-for-byte).
 */
function isDefaultLang(lang: string, defaultLang: string): boolean {
  return lang === defaultLang || lang === "default";
}

/**
 * Resolve the i18nString key a given authoring language writes to. Non-default
 * languages key by their own code; the default language pairs with the reserved
 * `default` key (the schema-required fallback).
 */
function langKey(lang: string, defaultLang: string): string {
  return isDefaultLang(lang, defaultLang) ? "default" : lang;
}

/**
 * Read the text an author sees for `lang`. For a non-default language this is the
 * raw lang-key value (NOT the resolved fallback) — the editor shows the empty box
 * for an untranslated string so the author knows it still falls back to `default`.
 */
export function getLangText(
  str: I18nString | I18nStringOptional | undefined,
  lang: string,
  defaultLang: string,
): string {
  if (!str) return "";
  const v = str[langKey(lang, defaultLang)];
  return typeof v === "string" ? v : "";
}

/**
 * Return a NEW i18n map with the `default` key set to `text`, preserving any
 * other language keys already present. Produces a valid #/$defs/i18nString
 * (the `default` key is always present).
 */
export function setDefaultText(
  str: I18nString | I18nStringOptional | undefined,
  text: string,
): I18nString {
  return { ...(str ?? {}), default: text } as I18nString;
}

/**
 * Write `text` into the right i18nString key for the active authoring language,
 * for a REQUIRED i18n map (headline / choice label / consent label). The `default`
 * key is always retained (the schema-required fallback):
 *   - default language → set the `default` key (single-language flow unchanged).
 *   - non-default language → set that lang key; if blank, DROP just that lang key
 *     (never touch `default`). Other languages' keys are preserved.
 */
export function setLangText(
  str: I18nString | I18nStringOptional | undefined,
  lang: string,
  text: string,
  defaultLang: string,
): I18nString {
  if (isDefaultLang(lang, defaultLang)) {
    return setDefaultText(str, text);
  }
  const next: I18nStringOptional = { ...(str ?? {}) };
  if (text.trim() === "") {
    delete next[lang];
  } else {
    next[lang] = text;
  }
  // A required i18n map always keeps a `default` key. If the loaded map somehow
  // lacked one, seed it empty so the result is still a valid #/$defs/i18nString.
  if (typeof next.default !== "string") next.default = "";
  return next as I18nString;
}

/**
 * Like setDefaultText but for OPTIONAL i18n maps (subheader/placeholder/labels):
 * when the text is blank we DROP the `default` key (and return undefined if the
 * map becomes empty) so we don't emit an empty optional string.
 */
export function setOptionalDefaultText(
  str: I18nStringOptional | undefined,
  text: string,
): I18nStringOptional | undefined {
  const next: I18nStringOptional = { ...(str ?? {}) };
  if (text.trim() === "") {
    delete next.default;
  } else {
    next.default = text;
  }
  return Object.keys(next).length === 0 ? undefined : next;
}

/**
 * Per-language writer for OPTIONAL i18n maps. Mirrors setOptionalDefaultText's
 * omit-when-blank rule, but per key:
 *   - default language → set/drop the `default` key (existing behavior).
 *   - non-default language → set/drop ONLY that lang key; `default` is never
 *     touched. The map returns undefined only once it is fully empty (so a blank
 *     translation can't strand an empty optional string).
 */
export function setOptionalLangText(
  str: I18nStringOptional | undefined,
  lang: string,
  text: string,
  defaultLang: string,
): I18nStringOptional | undefined {
  if (isDefaultLang(lang, defaultLang)) {
    return setOptionalDefaultText(str, text);
  }
  const next: I18nStringOptional = { ...(str ?? {}) };
  if (text.trim() === "") {
    delete next[lang];
  } else {
    next[lang] = text;
  }
  return Object.keys(next).length === 0 ? undefined : next;
}

// --------------------------------------------------------------------------- //
//  language management (survey.languages CRUD with default-invariant guards)  //
// --------------------------------------------------------------------------- //
/**
 * Append a new language. No-ops (returns the SAME survey) when the code is blank
 * or already declared, so the caller's reference-equality dirty check stays honest.
 * New languages are added enabled and non-default; the existing default is kept.
 */
export function addLanguage(survey: Survey, code: string, label?: string): Survey {
  const trimmed = code.trim();
  const langs = survey.languages ?? [];
  if (trimmed === "") return survey;
  if (langs.some((l) => l.code === trimmed)) return survey;
  const entry: Language = { code: trimmed, default: false };
  const trimmedLabel = label?.trim();
  if (trimmedLabel) entry.label = trimmedLabel;
  return { ...survey, languages: [...langs, entry] };
}

/**
 * Remove a language by code. Refuses (returns the SAME survey) to remove the
 * default language or the last remaining language — survey.languages must always
 * keep exactly one `default: true` entry. Stale per-string keys for the removed
 * language are left in place (harmless per schema; `resolveI18n` ignores them).
 */
export function removeLanguage(survey: Survey, code: string): Survey {
  const langs = survey.languages ?? [];
  const target = langs.find((l) => l.code === code);
  if (!target) return survey;
  if (target.default) return survey;
  if (langs.length <= 1) return survey;
  return { ...survey, languages: langs.filter((l) => l.code !== code) };
}

/**
 * Mark `code` as the survey default, flipping the previous default off so exactly
 * one entry has `default: true`. Refuses (same survey) when the code isn't declared.
 * The i18nString `default` key is NOT rewritten — `resolveI18n`'s fallback chain
 * already pairs the default-language code with the `default` key.
 */
export function setDefaultLanguage(survey: Survey, code: string): Survey {
  const langs = survey.languages ?? [];
  if (!langs.some((l) => l.code === code)) return survey;
  return {
    ...survey,
    languages: langs.map((l) => ({ ...l, default: l.code === code })),
  };
}

// --------------------------------------------------------------------------- //
//  question factories — schema-valid defaults per type                        //
// --------------------------------------------------------------------------- //
function defaultChoices(): Choice[] {
  // singleSelect/multiSelect require minItems:2 — seed two labelled choices.
  return [
    { id: `c_${shortId()}`, label: { default: "Option 1" } },
    { id: `c_${shortId()}`, label: { default: "Option 2" } },
  ];
}

const DEFAULT_HEADLINES: Record<CoreQuestionType, string> = {
  openText: "Your question",
  singleSelect: "Pick one",
  multiSelect: "Select all that apply",
  pictureChoice: "Pick an image",
  rating: "How would you rate this?",
  nps: "How likely are you to recommend us?",
  cta: "A message for you",
  consent: "Please confirm",
  date: "Pick a date",
  number: "Enter a number",
  ranking: "Rank these in order",
  matrix: "Rate each item",
  contactInfo: "Your contact details",
  address: "Your address",
  scheduleMeeting: "Book a time with us",
  fileUpload: "Upload a file",
};

/** pictureChoice requires options minItems:2 — seed two placeholder images. */
function defaultPictureOptions(): PictureChoiceOption[] {
  return [
    { id: `c_${shortId()}`, imageUrl: "https://placehold.co/600x400?text=Option+1", label: { default: "Option 1" } },
    { id: `c_${shortId()}`, imageUrl: "https://placehold.co/600x400?text=Option+2", label: { default: "Option 2" } },
  ];
}

/** matrix requires columns minItems:2 — seed two labelled columns. */
function defaultColumns(): Choice[] {
  return [
    { id: `c_${shortId()}`, label: { default: "Column 1" } },
    { id: `c_${shortId()}`, label: { default: "Column 2" } },
  ];
}

/** matrix requires rows minItems:1 — seed one labelled row. */
function defaultRows(): Choice[] {
  return [{ id: `c_${shortId()}`, label: { default: "Row 1" } }];
}

/**
 * Build a brand-new question of `type` with a schema-valid default `config`.
 * The id is unique within `existingIds`.
 */
export function makeQuestion(type: CoreQuestionType, existingIds: Iterable<string>): Question {
  const id = newQuestionId(existingIds);
  const base: Question = {
    id,
    type,
    headline: { default: DEFAULT_HEADLINES[type] },
    required: false,
  };

  switch (type) {
    case "openText":
      return { ...base, config: { inputType: "short" } };
    case "singleSelect":
      return { ...base, config: { choices: defaultChoices(), display: "list" } };
    case "multiSelect":
      return { ...base, config: { choices: defaultChoices() } };
    case "pictureChoice":
      // pictureChoice requires options minItems:2; single-select by default.
      return { ...base, config: { options: defaultPictureOptions(), allowMulti: false } };
    case "rating":
      return { ...base, config: { range: 5, scale: "star" } };
    case "nps":
      // NPS config is open (no required keys) — fixed 0..10 scale.
      return { ...base, config: {} };
    case "cta":
      // Not data-collecting unless required; ships an acknowledge button.
      return { ...base, config: { buttonLabel: { default: "Continue" }, dismissable: true } };
    case "consent":
      return { ...base, config: { label: { default: "I agree" } } };
    case "date":
      // date config is open (no required keys); ISO format is the default.
      return { ...base, config: { format: "YYYY-MM-DD" } };
    case "number":
      // number config is open (no required keys).
      return { ...base, config: {} };
    case "ranking":
      // ranking requires choices minItems:2.
      return { ...base, config: { choices: defaultChoices() } };
    case "matrix":
      // matrix requires rows (>=1), columns (>=2), selection.
      return {
        ...base,
        config: { rows: defaultRows(), columns: defaultColumns(), selection: "single" },
      };
    case "contactInfo":
      // contactInfo requires `fields` with >=1 sub-field; seed email shown.
      return { ...base, config: { fields: { email: { show: true, required: false } } } };
    case "address":
      // address requires `fields` with >=1 sub-field; seed the common minimal set.
      return {
        ...base,
        config: {
          fields: {
            addressLine1: { show: true, required: false },
            city: { show: true, required: false },
            zip: { show: true, required: false },
            country: { show: true, required: false },
          },
        },
      };
    case "scheduleMeeting":
      // scheduleMeeting requires a non-empty calLink (cal.com path).
      return { ...base, config: { calLink: "your-cal-username" } };
    case "fileUpload":
      // fileUpload config is open (no required keys); single file by default.
      return { ...base, config: { allowMultiple: false, maxFiles: 1, maxSizeMb: 10 } };
  }
}

// --------------------------------------------------------------------------- //
//  immutable list transforms over survey.questions                           //
// --------------------------------------------------------------------------- //
export function addQuestion(survey: Survey, q: Question): Survey {
  return { ...survey, questions: [...survey.questions, q] };
}

export function deleteQuestion(survey: Survey, questionId: string): Survey {
  return { ...survey, questions: survey.questions.filter((q) => q.id !== questionId) };
}

/** Replace the question with the same id (used by the editor on every field edit). */
export function updateQuestion(survey: Survey, updated: Question): Survey {
  return {
    ...survey,
    questions: survey.questions.map((q) => (q.id === updated.id ? updated : q)),
  };
}

/** Move the question at `index` one slot toward the start (no-op at the top). */
export function moveQuestionUp(survey: Survey, index: number): Survey {
  if (index <= 0 || index >= survey.questions.length) return survey;
  const qs = [...survey.questions];
  [qs[index - 1], qs[index]] = [qs[index], qs[index - 1]];
  return { ...survey, questions: qs };
}

/** Move the question at `index` one slot toward the end (no-op at the bottom). */
export function moveQuestionDown(survey: Survey, index: number): Survey {
  if (index < 0 || index >= survey.questions.length - 1) return survey;
  const qs = [...survey.questions];
  [qs[index], qs[index + 1]] = [qs[index + 1], qs[index]];
  return { ...survey, questions: qs };
}

// --------------------------------------------------------------------------- //
//  choice transforms (for select editors)                                     //
// --------------------------------------------------------------------------- //
export function addChoice(question: Question, label = "New option"): Question {
  const config = { ...(question.config ?? {}) };
  const choices: Choice[] = Array.isArray(config.choices) ? [...config.choices] : [];
  const id = newChoiceId(choices.map((c) => c.id));
  choices.push({ id, label: { default: label } });
  return { ...question, config: { ...config, choices } };
}

/**
 * Edit a choice label. Defaults to the `default` key (single-language flow); when
 * `lang`/`defaultLang` are supplied, writes that authoring language's key while
 * preserving the others (choice labels are REQUIRED i18n — `default` is invariant).
 */
export function updateChoiceLabel(
  question: Question,
  choiceId: string,
  text: string,
  lang = "default",
  defaultLang = "default",
): Question {
  const config = { ...(question.config ?? {}) };
  const choices: Choice[] = Array.isArray(config.choices) ? config.choices : [];
  const next = choices.map((c) =>
    c.id === choiceId ? { ...c, label: setLangText(c.label, lang, text, defaultLang) } : c,
  );
  return { ...question, config: { ...config, choices: next } };
}

export function setChoiceIsOther(question: Question, choiceId: string, isOther: boolean): Question {
  const config = { ...(question.config ?? {}) };
  const choices: Choice[] = Array.isArray(config.choices) ? config.choices : [];
  const next = choices.map((c) => (c.id === choiceId ? { ...c, isOther } : c));
  return { ...question, config: { ...config, choices: next } };
}

export function deleteChoice(question: Question, choiceId: string): Question {
  const config = { ...(question.config ?? {}) };
  const choices: Choice[] = Array.isArray(config.choices) ? config.choices : [];
  const next = choices.filter((c) => c.id !== choiceId);
  return { ...question, config: { ...config, choices: next } };
}

/** Shallow-merge new config keys onto a question (per-type setting edits). */
export function patchConfig(question: Question, patch: Record<string, unknown>): Question {
  return { ...question, config: { ...(question.config ?? {}), ...patch } };
}

// --------------------------------------------------------------------------- //
//  named choice-array transforms (matrix rows/columns)                        //
// --------------------------------------------------------------------------- //
/**
 * Like addChoice/updateChoiceLabel/deleteChoice but for an arbitrarily-NAMED
 * config array of choices (e.g. matrix `rows` / `columns`). Choice ids stay
 * unique within the SAME named array. `defaultLabel` seeds new entries.
 */
export function addNamedChoice(question: Question, key: string, label = "New option"): Question {
  const config = { ...(question.config ?? {}) };
  const list: Choice[] = Array.isArray(config[key]) ? [...config[key]] : [];
  const id = newChoiceId(list.map((c) => c.id));
  list.push({ id, label: { default: label } });
  return { ...question, config: { ...config, [key]: list } };
}

export function updateNamedChoiceLabel(
  question: Question,
  key: string,
  choiceId: string,
  text: string,
  lang = "default",
  defaultLang = "default",
): Question {
  const config = { ...(question.config ?? {}) };
  const list: Choice[] = Array.isArray(config[key]) ? config[key] : [];
  const next = list.map((c) =>
    c.id === choiceId ? { ...c, label: setLangText(c.label, lang, text, defaultLang) } : c,
  );
  return { ...question, config: { ...config, [key]: next } };
}

export function deleteNamedChoice(question: Question, key: string, choiceId: string): Question {
  const config = { ...(question.config ?? {}) };
  const list: Choice[] = Array.isArray(config[key]) ? config[key] : [];
  const next = list.filter((c) => c.id !== choiceId);
  return { ...question, config: { ...config, [key]: next } };
}

// --------------------------------------------------------------------------- //
//  pictureChoice option transforms (config.options: {id, imageUrl, label?})    //
// --------------------------------------------------------------------------- //
/** Append a new picture option (schema requires id + imageUrl). */
export function addPictureOption(question: Question, imageUrl = "", label = ""): Question {
  const config = { ...(question.config ?? {}) };
  const options: PictureChoiceOption[] = Array.isArray(config.options) ? [...config.options] : [];
  const id = newChoiceId(options.map((o) => o.id));
  const opt: PictureChoiceOption = { id, imageUrl };
  if (label.trim() !== "") opt.label = { default: label };
  options.push(opt);
  return { ...question, config: { ...config, options } };
}

/** Set a picture option's imageUrl (kept as-is; schema requires a URI on save). */
export function updatePictureOptionImage(
  question: Question,
  optionId: string,
  imageUrl: string,
): Question {
  const config = { ...(question.config ?? {}) };
  const options: PictureChoiceOption[] = Array.isArray(config.options) ? config.options : [];
  const next = options.map((o) => (o.id === optionId ? { ...o, imageUrl } : o));
  return { ...question, config: { ...config, options: next } };
}

/**
 * Set a picture option's OPTIONAL label (blank drops the active language's key).
 * Defaults to the `default` key; when `lang`/`defaultLang` are supplied, writes
 * that authoring language's key while preserving the others.
 */
export function updatePictureOptionLabel(
  question: Question,
  optionId: string,
  text: string,
  lang = "default",
  defaultLang = "default",
): Question {
  const config = { ...(question.config ?? {}) };
  const options: PictureChoiceOption[] = Array.isArray(config.options) ? config.options : [];
  const next = options.map((o) =>
    o.id === optionId ? { ...o, label: setOptionalLangText(o.label, lang, text, defaultLang) } : o,
  );
  return { ...question, config: { ...config, options: next } };
}

export function deletePictureOption(question: Question, optionId: string): Question {
  const config = { ...(question.config ?? {}) };
  const options: PictureChoiceOption[] = Array.isArray(config.options) ? config.options : [];
  const next = options.filter((o) => o.id !== optionId);
  return { ...question, config: { ...config, options: next } };
}

// --------------------------------------------------------------------------- //
//  contactInfo / address field transforms                                     //
// --------------------------------------------------------------------------- //
/**
 * Patch a single contactInfo sub-field (firstName/lastName/email/phone/company)
 * OR an address sub-field (addressLine1/.../country) — both store config.fields
 * keyed by sub-field name, so the same transform serves both.
 * `patch === null` REMOVES the sub-field (toggling it off). The schema requires
 * `fields` to keep at least one sub-field, but the builder guards that at the UI
 * level (it never lets the author remove the final field).
 */
export function setContactField(
  question: Question,
  key: string,
  patch: Record<string, unknown> | null,
): Question {
  const config = { ...(question.config ?? {}) };
  const fields = { ...((config.fields as Record<string, unknown>) ?? {}) };
  if (patch === null) {
    delete fields[key];
  } else {
    fields[key] = { ...((fields[key] as Record<string, unknown>) ?? {}), ...patch };
  }
  return { ...question, config: { ...config, fields } };
}

// --------------------------------------------------------------------------- //
//  styling transforms (the Style tab edits survey.styling)                    //
// --------------------------------------------------------------------------- //
const STYLING_COLOR_KEYS = [
  "brandColor",
  "questionColor",
  "backgroundColor",
  "buttonColor",
  "buttonTextColor",
] as const;

/**
 * Normalize a styling object into a CLEAN, schema-valid #/$defs/styling
 * (additionalProperties:false, all keys optional). The Style tab seeds its
 * color swatches from BRAND_DEFAULTS for display, so "back to default" is
 * expressed as omission — never a persisted token:
 *   - blank color strings → dropped (omit-when-default, like setOptionalDefaultText)
 *   - roundness → clamped to [0, 32] (the schema bounds); non-numeric dropped
 *   - hideProgressBar → kept only when true (false is the schema default)
 *   - cardArrangement → kept only when "casual" ("straight" is the default)
 *   - background → kept only for type color/image WITH a non-blank value;
 *     "none" (or a missing value) drops the key (so a stale value can't
 *     round-trip back into the picker)
 *   - logoUrl → trimmed; blank dropped
 */
export function cleanStyling(s: Styling): Styling {
  const out: Styling = {};
  for (const k of STYLING_COLOR_KEYS) {
    const v = s[k];
    if (typeof v === "string" && v.trim() !== "") out[k] = v;
  }
  if (typeof s.logoUrl === "string" && s.logoUrl.trim() !== "") {
    out.logoUrl = s.logoUrl.trim();
  }
  if (s.roundness !== undefined && s.roundness !== null) {
    const n = Number(s.roundness);
    if (!Number.isNaN(n)) out.roundness = Math.min(32, Math.max(0, n));
  }
  if (s.hideProgressBar === true) out.hideProgressBar = true;
  if (s.cardArrangement === "casual") out.cardArrangement = "casual";
  const bg = s.background;
  if (bg && (bg.type === "color" || bg.type === "image")) {
    const value = typeof bg.value === "string" ? bg.value.trim() : "";
    if (value !== "") out.background = { type: bg.type, value };
  }
  return out;
}

/**
 * Shallow-merge a partial styling `patch` onto the survey's styling, then clean
 * it (see cleanStyling). Returns a FRESH Survey so the builder's reference-equality
 * dirty-tracking fires. To CLEAR a color back to the brand default, pass it as "".
 */
export function patchStyling(survey: Survey, patch: Partial<Styling>): Survey {
  const merged: Styling = { ...(survey.styling ?? {}), ...patch };
  return { ...survey, styling: cleanStyling(merged) };
}

// --------------------------------------------------------------------------- //
//  settings transforms (the Settings tab edits single_use/capture_ip/close_on) //
// --------------------------------------------------------------------------- //
/** Toggle single-use response links. Returns a fresh Survey (dirty-tracking). */
export function setSingleUse(survey: Survey, value: boolean): Survey {
  return { ...survey, single_use: value };
}

/** Toggle respondent-IP capture (privacy; off by default). Fresh Survey. */
export function setCaptureIp(survey: Survey, value: boolean): Survey {
  return { ...survey, capture_ip: value };
}

/**
 * Normalize close conditions to a clean shape (survey-schema.json #/$defs/closeConditions):
 *   - by_date: kept only when a non-blank string (ISO 8601 UTC)
 *   - by_response_cap: kept only when an integer >= 1 (the schema minimum)
 * Returns `null` when neither is set, so an empty/cleared form persists as "no auto-close".
 */
export function cleanCloseConditions(c: CloseConditions): CloseConditions | null {
  const out: CloseConditions = {};
  if (typeof c.by_date === "string" && c.by_date.trim() !== "") out.by_date = c.by_date;
  if (c.by_response_cap !== undefined && c.by_response_cap !== null) {
    const n = Number(c.by_response_cap);
    if (!Number.isNaN(n) && n >= 1) out.by_response_cap = Math.floor(n);
  }
  return Object.keys(out).length === 0 ? null : out;
}

/**
 * Merge a partial close-condition patch onto the survey's `close_on`, then clean it.
 * Pass `by_date: ""` or `by_response_cap: undefined` to clear that key. Fresh Survey.
 */
export function patchCloseConditions(survey: Survey, patch: Partial<CloseConditions>): Survey {
  const merged: CloseConditions = { ...(survey.close_on ?? {}), ...patch };
  return { ...survey, close_on: cleanCloseConditions(merged) };
}

// --------------------------------------------------------------------------- //
//  app-survey settings (survey_type / triggers / display_options — WS-11)      //
//                                                                             //
//  Distribution-channel controls the Settings tab edits. These mirror the     //
//  setSingleUse/setCaptureIp pattern: each returns a FRESH Survey so the       //
//  builder's reference-equality dirty check fires. They map straight onto the  //
//  snake_case SurveyWrite fields the server already carries (survey_type /     //
//  triggers / display_options); link surveys never touch them, so a link       //
//  survey's wire body stays byte-identical to today (see toSurveyWrite).       //
// --------------------------------------------------------------------------- //
/** Set the distribution channel (`"link"` shareable URL vs `"app"` in-product widget). Fresh Survey. */
export function setSurveyType(survey: Survey, surveyType: "link" | "app"): Survey {
  return { ...survey, survey_type: surveyType };
}

/**
 * Replace the list of ActionClass ids that trigger this app survey. Only meaningful when
 * survey_type==="app"; stored verbatim (the server resolves them to real ActionClasses). Fresh
 * Survey. Pass [] to clear all triggers.
 */
export function setTriggers(survey: Survey, triggers: string[]): Survey {
  return { ...survey, triggers: [...triggers] };
}

/**
 * Toggle a single ActionClass id in the survey's triggers (add when absent, remove when present).
 * A convenience over setTriggers for the multi-select checkboxes. Fresh Survey.
 */
export function toggleTrigger(survey: Survey, actionClassId: string): Survey {
  const current = survey.triggers ?? [];
  const next = current.includes(actionClassId)
    ? current.filter((id) => id !== actionClassId)
    : [...current, actionClassId];
  return { ...survey, triggers: next };
}

/**
 * Normalize app-survey display options to a clean shape ({displayLimit?, displayPercentage?}):
 *   - displayLimit: kept only when an integer >= 1 (a blank/NaN/sub-1 value drops the key).
 *   - displayPercentage: kept only when a number in [0, 100] (clamped; blank/NaN drops the key).
 * Returns `{}` when neither is set (so a cleared form persists as "no display limits").
 */
export function cleanDisplayOptions(
  o: { displayLimit?: number; displayPercentage?: number },
): { displayLimit?: number; displayPercentage?: number } {
  const out: { displayLimit?: number; displayPercentage?: number } = {};
  if (o.displayLimit !== undefined && o.displayLimit !== null) {
    const n = Number(o.displayLimit);
    if (!Number.isNaN(n) && n >= 1) out.displayLimit = Math.floor(n);
  }
  if (o.displayPercentage !== undefined && o.displayPercentage !== null) {
    const n = Number(o.displayPercentage);
    if (!Number.isNaN(n)) out.displayPercentage = Math.min(100, Math.max(0, n));
  }
  return out;
}

/**
 * Merge a partial display-options patch onto the survey's `display_options`, then clean it.
 * Pass `displayLimit: undefined` / `displayPercentage: undefined` to clear that key. Fresh Survey.
 */
export function patchDisplayOptions(
  survey: Survey,
  patch: { displayLimit?: number; displayPercentage?: number },
): Survey {
  const merged = { ...(survey.display_options ?? {}), ...patch };
  return { ...survey, display_options: cleanDisplayOptions(merged) };
}

// --------------------------------------------------------------------------- //
//  hidden fields (survey.hiddenFields CRUD — survey-schema.json #/$defs/hiddenFields) //
// --------------------------------------------------------------------------- //
/** A hidden-field id matches the question-id charset (it shares the recall namespace). */
const HIDDEN_FIELD_ID_RE = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;

/** True if `id` is a schema-valid hidden-field id. */
export function isValidHiddenFieldId(id: string): boolean {
  return HIDDEN_FIELD_ID_RE.test(id);
}

/**
 * Append a hidden field {id, name?}. NO-OPS (returns the SAME survey, so the builder's
 * reference-equality dirty check stays honest) when the id is blank, schema-invalid, or
 * already declared, OR collides with an existing question/ending id (the recall +
 * Response.data namespace is shared — the server's duplicate_id check would reject it).
 */
export function addHiddenField(survey: Survey, id: string, name?: string): Survey {
  const trimmed = id.trim();
  if (!isValidHiddenFieldId(trimmed)) return survey;
  const fields = survey.hiddenFields ?? [];
  if (fields.some((f) => f.id === trimmed)) return survey;
  // Reject collisions with question / ending ids (shared id namespace).
  const taken = new Set<string>([
    ...survey.questions.map((q) => q.id),
    ...(survey.endings ?? []).map((e) => e.id),
  ]);
  if (taken.has(trimmed)) return survey;
  const entry: HiddenField = { id: trimmed };
  const trimmedName = name?.trim();
  if (trimmedName) entry.name = trimmedName;
  return { ...survey, hiddenFields: [...fields, entry] };
}

/** Remove a hidden field by id. No-ops (same survey) when the id isn't declared. */
export function removeHiddenField(survey: Survey, id: string): Survey {
  const fields = survey.hiddenFields ?? [];
  if (!fields.some((f) => f.id === id)) return survey;
  return { ...survey, hiddenFields: fields.filter((f) => f.id !== id) };
}

// --------------------------------------------------------------------------- //
//  variables (survey.variables CRUD — survey-schema.json #/$defs/variable)    //
// --------------------------------------------------------------------------- //
/** A variable id matches the question-id charset (it shares the recall/condition namespace). */
export function isValidVariableId(id: string): boolean {
  return HIDDEN_FIELD_ID_RE.test(id);
}

/**
 * Append a variable {id, name, type, initialValue}. NO-OPS (returns the SAME survey, so the
 * builder's reference-equality dirty check stays honest) when the id is blank/schema-invalid or
 * collides with ANY existing id in the shared namespace (question / ending / hidden field /
 * variable — the server's duplicate_id check would reject it). The initial value is seeded to a
 * type-correct default (0 for number, "" for text); the author can edit it afterwards.
 */
export function addVariable(
  survey: Survey,
  id: string,
  name: string,
  type: "number" | "text",
): Survey {
  const trimmedId = id.trim();
  if (!isValidVariableId(trimmedId)) return survey;
  const vars = survey.variables ?? [];
  // Reject collisions across the WHOLE shared id namespace.
  const taken = new Set<string>([
    ...survey.questions.map((q) => q.id),
    ...(survey.endings ?? []).map((e) => e.id),
    ...(survey.hiddenFields ?? []).map((f) => f.id),
    ...vars.map((v) => v.id),
  ]);
  if (taken.has(trimmedId)) return survey;
  const trimmedName = name.trim();
  const entry: Variable = {
    id: trimmedId,
    name: trimmedName || trimmedId,
    type,
    initialValue: type === "number" ? 0 : "",
  };
  return { ...survey, variables: [...vars, entry] };
}

/** Remove a variable by id. No-ops (same survey) when the id isn't declared. Does NOT rewrite
 *  logic that referenced it — validate_survey reports `unknown_variable` / `unknown_calc_ref` /
 *  a missing condition ref on Save, surfaced via the error banner (same UX as deleting a
 *  referenced question/ending). */
export function removeVariable(survey: Survey, id: string): Survey {
  const vars = survey.variables ?? [];
  if (!vars.some((v) => v.id === id)) return survey;
  return { ...survey, variables: vars.filter((v) => v.id !== id) };
}

/** Set a variable's initial value (type-coerced: number for number vars, string for text). */
export function setVariableInitialValue(
  survey: Survey,
  id: string,
  raw: string,
): Survey {
  const vars = survey.variables ?? [];
  const target = vars.find((v) => v.id === id);
  if (!target) return survey;
  const initialValue: number | string =
    target.type === "number" ? (raw.trim() === "" ? 0 : Number(raw)) : raw;
  // Guard NaN on number vars -> keep 0 (a non-numeric initial would fail server validation).
  const safeValue =
    target.type === "number" && typeof initialValue === "number" && Number.isNaN(initialValue)
      ? 0
      : initialValue;
  return {
    ...survey,
    variables: vars.map((v) => (v.id === id ? { ...v, initialValue: safeValue } : v)),
  };
}

/** Set a variable's human label (falls back to the id when whitespace-only — name is required). */
export function setVariableName(survey: Survey, id: string, name: string): Survey {
  const vars = survey.variables ?? [];
  const target = vars.find((v) => v.id === id);
  if (!target) return survey;
  const next = name.trim() || target.id;
  return { ...survey, variables: vars.map((v) => (v.id === id ? { ...v, name: next } : v)) };
}

// --------------------------------------------------------------------------- //
//  follow-ups (survey.followUps CRUD — survey-schema.json #/$defs/followUp)   //
// --------------------------------------------------------------------------- //
/** A new follow-up id (question-id charset; the FollowUpDelivery dedup key per response). */
export function newFollowUpId(existing: Iterable<string>): string {
  const taken = new Set(existing);
  let id = `fu_${shortId()}`;
  while (taken.has(id)) id = `fu_${shortId()}`;
  return id;
}

/**
 * Append a new follow-up with sensible schema-valid defaults: an unconditional
 * responseFinished trigger + a sendEmail action to a (blank) fixedEmail recipient and a
 * minimal i18n subject/body whose `default` key is present. The author then edits it. The
 * blank fixedEmail will fail server validation until filled — surfaced like any other
 * validation error on save (matching how a blank hidden-field id is handled).
 */
export function addFollowUp(survey: Survey, name?: string): Survey {
  const fus = survey.followUps ?? [];
  const id = newFollowUpId(fus.map((f) => f.id));
  const fu: FollowUp = {
    id,
    trigger: { type: "responseFinished" },
    action: {
      type: "sendEmail",
      recipient: { type: "fixedEmail", email: "" },
      subject: { default: "We received your response" },
      body: { default: "<p>Thanks for completing our survey.</p>" },
    },
  };
  const trimmedName = name?.trim();
  if (trimmedName) fu.name = trimmedName;
  return { ...survey, followUps: [...fus, fu] };
}

/** Remove a follow-up by id. No-ops (same survey) when the id isn't present. */
export function removeFollowUp(survey: Survey, id: string): Survey {
  const fus = survey.followUps ?? [];
  if (!fus.some((f) => f.id === id)) return survey;
  return { ...survey, followUps: fus.filter((f) => f.id !== id) };
}

/** Replace a follow-up in place (by id). No-ops (same survey) when the id isn't present. */
export function updateFollowUp(survey: Survey, updated: FollowUp): Survey {
  const fus = survey.followUps ?? [];
  if (!fus.some((f) => f.id === updated.id)) return survey;
  return { ...survey, followUps: fus.map((f) => (f.id === updated.id ? updated : f)) };
}

/** Set a follow-up's human label (or clear it when whitespace-only).
 *
 * Stores the name AS TYPED (no mid-edit trimming — that collapses interior spaces in a
 * controlled input). A whitespace-only value clears the label entirely. */
export function setFollowUpName(survey: Survey, id: string, name: string): Survey {
  const fus = survey.followUps ?? [];
  const fu = fus.find((f) => f.id === id);
  if (!fu) return survey;
  const next: FollowUp = { ...fu };
  if (name.trim()) next.name = name;
  else delete next.name;
  return updateFollowUp(survey, next);
}

/** Set a follow-up's recipient (the discriminated #/$defs/followUpRecipient object). */
export function setFollowUpRecipient(
  survey: Survey,
  id: string,
  recipient: FollowUpRecipient,
): Survey {
  const fu = (survey.followUps ?? []).find((f) => f.id === id);
  if (!fu) return survey;
  return updateFollowUp(survey, { ...fu, action: { ...fu.action, recipient } });
}

/**
 * Set a follow-up's trigger condition. Pass `null` to clear it (unconditional — fire on
 * every finish); pass a ConditionGroup to gate the fire. The trigger type stays
 * `responseFinished` (the only v1 trigger).
 */
export function setFollowUpCondition(
  survey: Survey,
  id: string,
  condition: ConditionGroup | null,
): Survey {
  const fu = (survey.followUps ?? []).find((f) => f.id === id);
  if (!fu) return survey;
  const trigger =
    condition == null
      ? { type: "responseFinished" as const }
      : { type: "responseFinished" as const, condition };
  return updateFollowUp(survey, { ...fu, trigger });
}

/** Set the follow-up email's subject text for a language (i18n-aware; preserves `default`). */
export function setFollowUpSubject(
  survey: Survey,
  id: string,
  lang: string,
  text: string,
  defaultLang: string,
): Survey {
  const fu = (survey.followUps ?? []).find((f) => f.id === id);
  if (!fu) return survey;
  // setLangText handles the default-language case (writes `default`) AND the non-default
  // case (writes that lang key, preserving `default`) — a valid i18nString either way.
  const subject = setLangText(fu.action.subject, lang, text, defaultLang);
  return updateFollowUp(survey, { ...fu, action: { ...fu.action, subject } });
}

/** Set the follow-up email's HTML body for a language (i18n-aware; preserves `default`). */
export function setFollowUpBody(
  survey: Survey,
  id: string,
  lang: string,
  html: string,
  defaultLang: string,
): Survey {
  const fu = (survey.followUps ?? []).find((f) => f.id === id);
  if (!fu) return survey;
  const body = setLangText(fu.action.body, lang, html, defaultLang);
  return updateFollowUp(survey, { ...fu, action: { ...fu.action, body } });
}

// --------------------------------------------------------------------------- //
//  quotas (survey.quotas CRUD — survey-schema.json #/$defs/quota)             //
// --------------------------------------------------------------------------- //
/** A new quota id (question-id charset; survey-unique among quotas). */
export function newQuotaId(existing: Iterable<string>): string {
  const taken = new Set(existing);
  let id = `quota_${shortId()}`;
  while (taken.has(id)) id = `quota_${shortId()}`;
  return id;
}

/**
 * Append a new quota with sensible schema-valid defaults: limit 100 + a single condition leaf
 * against the first available answer source (a question/hidden-field id) so the condition is
 * shaped correctly, and NO endingId (the runtime shows a built-in 'quota full' screen until the
 * author picks one). When the survey has no questions/hidden fields the leaf has an empty
 * leftQuestionId — that fails server validation until a source is picked, surfaced like any other
 * validation error on save (matching how a blank follow-up email is handled).
 */
export function addQuota(survey: Survey, name = "New quota"): Survey {
  const quotas = survey.quotas ?? [];
  const id = newQuotaId(quotas.map((q) => q.id));
  const firstSource =
    survey.questions[0]?.id ?? survey.hiddenFields?.[0]?.id ?? "";
  const quota: Quota = {
    id,
    name: name.trim() || "New quota",
    limit: 100,
    condition: { leftQuestionId: firstSource, operator: "equals", value: "" },
  };
  return { ...survey, quotas: [...quotas, quota] };
}

/** Remove a quota by id. No-ops (same survey) when the id isn't present. */
export function removeQuota(survey: Survey, id: string): Survey {
  const quotas = survey.quotas ?? [];
  if (!quotas.some((q) => q.id === id)) return survey;
  return { ...survey, quotas: quotas.filter((q) => q.id !== id) };
}

/** Replace a quota in place (by id). No-ops (same survey) when the id isn't present. */
function updateQuota(survey: Survey, updated: Quota): Survey {
  const quotas = survey.quotas ?? [];
  if (!quotas.some((q) => q.id === updated.id)) return survey;
  return { ...survey, quotas: quotas.map((q) => (q.id === updated.id ? updated : q)) };
}

/**
 * Set a quota's human label (internal — no i18n). Stores the name AS TYPED (no mid-edit trimming
 * — that collapses interior spaces + fights a controlled input, the lesson from setFollowUpName).
 * A whitespace-only value is stored as-is; the schema requires a non-empty name (minLength 1), so
 * a blank name fails server validation on Save, surfaced like any other validation error (matching
 * how a blank follow-up email / hidden-field id is handled).
 */
export function setQuotaName(survey: Survey, id: string, name: string): Survey {
  const quota = (survey.quotas ?? []).find((q) => q.id === id);
  if (!quota) return survey;
  return updateQuota(survey, { ...quota, name });
}

/**
 * Set a quota's response limit. Coerces to an integer >= 1 (a blank/NaN/sub-1 value clamps to 1)
 * so the stored value always satisfies the schema's `minimum: 1`.
 */
export function setQuotaLimit(survey: Survey, id: string, raw: string | number): Survey {
  const quota = (survey.quotas ?? []).find((q) => q.id === id);
  if (!quota) return survey;
  const n = typeof raw === "number" ? raw : Number(String(raw).trim());
  const limit = Number.isFinite(n) ? Math.max(1, Math.floor(n)) : 1;
  return updateQuota(survey, { ...quota, limit });
}

/** Set a quota's matching condition (a ConditionGroup — the same shape logic + follow-ups use). */
export function setQuotaCondition(
  survey: Survey,
  id: string,
  condition: ConditionGroup,
): Survey {
  const quota = (survey.quotas ?? []).find((q) => q.id === id);
  if (!quota) return survey;
  return updateQuota(survey, { ...quota, condition });
}

/**
 * Set (or clear) a quota's over-quota target ending. Pass an empty string / undefined to CLEAR it
 * (the runtime then shows the built-in 'quota full' screen); pass an ending id to route there.
 */
export function setQuotaEndingId(
  survey: Survey,
  id: string,
  endingId: string | undefined,
): Survey {
  const quota = (survey.quotas ?? []).find((q) => q.id === id);
  if (!quota) return survey;
  const next: Quota = { ...quota };
  if (endingId && endingId.trim()) next.endingId = endingId;
  else delete next.endingId;
  return updateQuota(survey, next);
}

// --------------------------------------------------------------------------- //
//  welcome card (survey-schema.json #/$defs/welcomeCard)                       //
// --------------------------------------------------------------------------- //
/**
 * Turn the welcome card ON. NO-OPS (returns the SAME survey) when it is already
 * enabled, so the builder's reference-equality dirty check stays honest. Seeds a
 * schema-valid card: `enabled:true` + a `headline` whose `default` key is present
 * (the schema-required i18nString fallback). Any other languages an author later
 * adds are layered via setLangText, which preserves `default`.
 */
export function enableWelcomeCard(survey: Survey, headline = "Welcome"): Survey {
  if (survey.welcomeCard?.enabled === true) return survey;
  const existing = survey.welcomeCard ?? undefined;
  const card: WelcomeCard = {
    ...(existing ?? {}),
    enabled: true,
    // Keep a previously-authored headline if one was retained; otherwise seed one.
    headline: existing?.headline ?? { default: headline },
  };
  return { ...survey, welcomeCard: card };
}

/**
 * Turn the welcome card OFF. Sets `welcomeCard: null` (the current default / no-welcome
 * shape the runtime + preview already handle). NO-OPS when it is already null/undefined
 * AND was not enabled, so an untouched no-welcome survey stays byte-identical (the
 * dirty flag never trips). Clearing DISCARDS the authored content — re-enabling reseeds.
 */
export function disableWelcomeCard(survey: Survey): Survey {
  if (survey.welcomeCard == null) return survey;
  return { ...survey, welcomeCard: null };
}

/**
 * Edit the welcome card's REQUIRED headline (i18nString — `default` invariant) for the
 * active authoring language. NO-OPS when the card isn't enabled (you can't edit a card
 * that isn't there). Uses setLangText so the default language writes the `default` key
 * (single-language flow unchanged) and a non-default language writes its own key while
 * preserving `default` + the other languages.
 */
export function setWelcomeHeadline(
  survey: Survey,
  text: string,
  lang = "default",
  defaultLang = "default",
): Survey {
  const card = survey.welcomeCard;
  if (!card || card.enabled !== true) return survey;
  return {
    ...survey,
    welcomeCard: { ...card, headline: setLangText(card.headline, lang, text, defaultLang) },
  };
}

/**
 * Edit the welcome card's OPTIONAL rich-text body (`html`) for the active language.
 * NO-OPS when the card isn't enabled. Blank drops the active language's key (omit-when-
 * blank); the key set vanishes only once fully empty — see setOptionalLangText.
 */
export function setWelcomeHtml(
  survey: Survey,
  text: string,
  lang = "default",
  defaultLang = "default",
): Survey {
  const card = survey.welcomeCard;
  if (!card || card.enabled !== true) return survey;
  return {
    ...survey,
    welcomeCard: { ...card, html: setOptionalLangText(card.html, lang, text, defaultLang) },
  };
}

/**
 * Edit the welcome card's OPTIONAL start-button label (`buttonLabel`) for the active
 * language. NO-OPS when the card isn't enabled. Blank drops the language's key.
 */
export function setWelcomeButtonLabel(
  survey: Survey,
  text: string,
  lang = "default",
  defaultLang = "default",
): Survey {
  const card = survey.welcomeCard;
  if (!card || card.enabled !== true) return survey;
  return {
    ...survey,
    welcomeCard: {
      ...card,
      buttonLabel: setOptionalLangText(card.buttonLabel, lang, text, defaultLang),
    },
  };
}

// --------------------------------------------------------------------------- //
//  endings (survey-schema.json #/$defs/ending — endScreen | redirect)         //
// --------------------------------------------------------------------------- //
/**
 * Generate a schema-valid ending id (matches #/$defs/questionId), guaranteed unique
 * across the WHOLE shared id namespace — questions + existing endings + hidden fields —
 * so a freshly-added ending never trips validateSurvey's duplicate_id check (the
 * recall/Response.data + logic-target namespace is shared). Stable once created: update
 * and reorder never rewrite ids, so any logic that already targets an ending id (via
 * endSurvey.endingId or jumpTo.target) keeps resolving.
 */
export function newEndingId(survey: Survey): string {
  const taken = new Set<string>([
    ...survey.questions.map((q) => q.id),
    ...(survey.endings ?? []).map((e) => e.id),
    ...(survey.hiddenFields ?? []).map((f) => f.id),
  ]);
  let id = `e_${shortId()}`;
  while (taken.has(id)) id = `e_${shortId()}`;
  return id;
}

/**
 * Append a new ending of `type`. The id is freshly generated + namespace-unique
 * (newEndingId). An `endScreen` is created minimal (`{id,type}`) — the runtime falls
 * back to its warm default thank-you copy when no content is set. A `redirect` is
 * created with an empty `redirectUrl` (the schema REQUIRES the key; the author fills
 * the URL in — the server's JSON-schema validation enforces `format: uri` on save,
 * surfaced via the same 400 banner that other URL/color fields use; the frontend
 * logic-only validateSurvey does not pre-gate it).
 */
export function addEnding(survey: Survey, type: Ending["type"] = "endScreen"): Survey {
  const id = newEndingId(survey);
  const ending: Ending = type === "redirect" ? { id, type, redirectUrl: "" } : { id, type };
  return { ...survey, endings: [...(survey.endings ?? []), ending] };
}

/**
 * Replace the ending with the same id (the editor calls this on every field edit).
 * The id is NEVER changed here — callers pass the same id back — so logic targets stay
 * stable. NO-OPS (same survey) when no ending has that id.
 */
export function updateEnding(survey: Survey, updated: Ending): Survey {
  const endings = survey.endings ?? [];
  if (!endings.some((e) => e.id === updated.id)) return survey;
  return { ...survey, endings: endings.map((e) => (e.id === updated.id ? updated : e)) };
}

/**
 * Change an ending's `type` (endScreen <-> redirect) while KEEPING its id + any shared
 * content fields (headline/subheader/buttonLabel/buttonLink are valid on both per the
 * schema's additionalProperties:false-but-shared shape, so we don't strip them).
 * Switching TO redirect seeds an empty `redirectUrl` when none exists (the key is
 * required on redirect); switching to endScreen drops `redirectUrl` (it isn't used).
 * NO-OPS when the id isn't found or the type is unchanged.
 */
export function setEndingType(survey: Survey, id: string, type: Ending["type"]): Survey {
  const endings = survey.endings ?? [];
  const target = endings.find((e) => e.id === id);
  if (!target || target.type === type) return survey;
  const next: Ending = { ...target, type };
  if (type === "redirect") {
    if (typeof next.redirectUrl !== "string") next.redirectUrl = "";
  } else {
    delete next.redirectUrl;
  }
  return { ...survey, endings: endings.map((e) => (e.id === id ? next : e)) };
}

/**
 * Edit one of an ending's OPTIONAL i18n text fields (headline / subheader / buttonLabel)
 * for the active authoring language. Blank drops the language's key (omit-when-blank via
 * setOptionalLangText) — and the whole field is removed once it is fully empty, so a
 * cleared field never persists as an empty optional map. NO-OPS when the id isn't found.
 */
export function setEndingText(
  survey: Survey,
  id: string,
  field: "headline" | "subheader" | "buttonLabel",
  text: string,
  lang = "default",
  defaultLang = "default",
): Survey {
  const endings = survey.endings ?? [];
  const target = endings.find((e) => e.id === id);
  if (!target) return survey;
  const nextField = setOptionalLangText(target[field], lang, text, defaultLang);
  const next: Ending = { ...target };
  if (nextField === undefined) {
    delete next[field];
  } else {
    next[field] = nextField;
  }
  return { ...survey, endings: endings.map((e) => (e.id === id ? next : e)) };
}

/**
 * Set a plain (non-i18n) URL field on an ending — `redirectUrl` (redirect target) or
 * `buttonLink` (endScreen secondary-CTA href). Blank DROPS the key on buttonLink; for
 * redirectUrl we KEEP the empty string (the schema requires the key on a redirect, and
 * the server validates `format: uri` on save). NO-OPS when the id isn't found.
 */
export function setEndingUrl(
  survey: Survey,
  id: string,
  field: "redirectUrl" | "buttonLink",
  url: string,
): Survey {
  const endings = survey.endings ?? [];
  const target = endings.find((e) => e.id === id);
  if (!target) return survey;
  const next: Ending = { ...target };
  const trimmed = url.trim();
  if (field === "buttonLink") {
    if (trimmed === "") delete next.buttonLink;
    else next.buttonLink = trimmed;
  } else {
    // redirectUrl: keep the (possibly empty) string so the required key persists.
    next.redirectUrl = trimmed;
  }
  return { ...survey, endings: endings.map((e) => (e.id === id ? next : e)) };
}

/**
 * Remove an ending by id. NO-OPS (same survey) when the id isn't declared. Does NOT
 * rewrite any logic that referenced the removed ending — validateSurvey already reports
 * `unknown_target` for a now-dangling endSurvey.endingId / jumpTo.target, surfaced via the
 * Save/Publish error banner (identical UX to deleting a referenced question), so the author
 * is told rather than silently losing a branch.
 */
export function removeEnding(survey: Survey, id: string): Survey {
  const endings = survey.endings ?? [];
  if (!endings.some((e) => e.id === id)) return survey;
  return { ...survey, endings: endings.filter((e) => e.id !== id) };
}

/** Move the ending at `index` one slot toward the start (no-op at the top). Ids stay stable. */
export function moveEndingUp(survey: Survey, index: number): Survey {
  const endings = survey.endings ?? [];
  if (index <= 0 || index >= endings.length) return survey;
  const next = [...endings];
  [next[index - 1], next[index]] = [next[index], next[index - 1]];
  return { ...survey, endings: next };
}

/** Move the ending at `index` one slot toward the end (no-op at the bottom). Ids stay stable. */
export function moveEndingDown(survey: Survey, index: number): Survey {
  const endings = survey.endings ?? [];
  if (index < 0 || index >= endings.length - 1) return survey;
  const next = [...endings];
  [next[index], next[index + 1]] = [next[index + 1], next[index]];
  return { ...survey, endings: next };
}

// --------------------------------------------------------------------------- //
//  blocks (survey-schema.json #/$defs/block — additive grouping overlay)      //
//                                                                             //
//  Blocks are a PURE METADATA OVERLAY: the flat `questions[]` array remains   //
//  the single source of truth for flow order + logic. These transforms NEVER  //
//  touch `survey.questions` — they only read it (to validate refs) and edit   //
//  `survey.blocks`. Every transform returns a FRESH Survey (or the SAME survey //
//  on a no-op) so the builder's reference-equality dirty check stays honest,  //
//  and every result is schema-valid (well-formed #/$defs/block items + the    //
//  in-code cross-ref invariants: questionIds resolve, no question in two       //
//  blocks, unique block ids).                                                 //
// --------------------------------------------------------------------------- //
/** A new block id (question-id charset; unique among existing block ids). */
export function newBlockId(existing: Iterable<string>): string {
  const taken = new Set(existing);
  let id = `b_${shortId()}`;
  while (taken.has(id)) id = `b_${shortId()}`;
  return id;
}

/** The set of question ids already claimed by some block (each question is in ≤1 block). */
function claimedQuestionIds(survey: Survey): Set<string> {
  const claimed = new Set<string>();
  for (const b of survey.blocks ?? []) {
    for (const qid of b.questionIds) claimed.add(qid);
  }
  return claimed;
}

/**
 * Create a new (empty) block with an optional title and randomize mode. Appends to
 * `survey.blocks`. The id is freshly generated + unique among existing block ids. The new
 * block groups nothing until questions are added (addQuestionToBlock) — harmless + valid.
 */
export function createBlock(
  survey: Survey,
  title?: string,
  randomize: BlockRandomize = "none",
): Survey {
  const blocks = survey.blocks ?? [];
  const block: Block = { id: newBlockId(blocks.map((b) => b.id)), questionIds: [] };
  const trimmedTitle = title?.trim();
  if (trimmedTitle) block.title = trimmedTitle;
  if (randomize !== "none") block.randomize = randomize;
  return { ...survey, blocks: [...blocks, block] };
}

/**
 * Add a question to a block. NO-OPS (returns the SAME survey) when: the block id isn't found,
 * the question id isn't a real question, or the question is ALREADY in this block. If the
 * question is in ANOTHER block, it is MOVED here (removed from the other block first) so the
 * "a question is in at most one block" invariant always holds. The question is appended to
 * the block's display order. NEVER touches `survey.questions`.
 */
export function addQuestionToBlock(survey: Survey, blockId: string, questionId: string): Survey {
  const blocks = survey.blocks ?? [];
  const target = blocks.find((b) => b.id === blockId);
  if (!target) return survey;
  // Must be a real question (refs that don't resolve would fail server validation).
  if (!survey.questions.some((q) => q.id === questionId)) return survey;
  if (target.questionIds.includes(questionId)) return survey; // already here — no-op.
  const next = blocks.map((b) => {
    if (b.id === blockId) {
      return { ...b, questionIds: [...b.questionIds, questionId] };
    }
    // Remove from any OTHER block (enforce single membership).
    if (b.questionIds.includes(questionId)) {
      return { ...b, questionIds: b.questionIds.filter((id) => id !== questionId) };
    }
    return b;
  });
  return { ...survey, blocks: next };
}

/**
 * Remove a question from a block (ungroup just that one question). NO-OPS when the block id
 * isn't found or the question isn't in it. The question STAYS in `survey.questions` (blocks
 * never own questions) — only the grouping reference is dropped.
 */
export function removeQuestionFromBlock(
  survey: Survey,
  blockId: string,
  questionId: string,
): Survey {
  const blocks = survey.blocks ?? [];
  const target = blocks.find((b) => b.id === blockId);
  if (!target || !target.questionIds.includes(questionId)) return survey;
  return {
    ...survey,
    blocks: blocks.map((b) =>
      b.id === blockId
        ? { ...b, questionIds: b.questionIds.filter((id) => id !== questionId) }
        : b,
    ),
  };
}

/**
 * Set a block's human title (or clear it when whitespace-only). NO-OPS when the id isn't found.
 * Stores the title AS TYPED (no mid-edit trimming, mirroring setFollowUpName) — but clears the
 * key entirely on a whitespace-only value so an empty title never persists.
 */
export function setBlockTitle(survey: Survey, blockId: string, title: string): Survey {
  const blocks = survey.blocks ?? [];
  const target = blocks.find((b) => b.id === blockId);
  if (!target) return survey;
  const next: Block = { ...target };
  if (title.trim()) next.title = title;
  else delete next.title;
  return { ...survey, blocks: blocks.map((b) => (b.id === blockId ? next : b)) };
}

/**
 * Set a block's within-block randomization mode. NO-OPS when the id isn't found. "none" DROPS
 * the key (omit-when-default — keeps a no-randomize block byte-identical to the schema default).
 */
export function setBlockRandomize(
  survey: Survey,
  blockId: string,
  randomize: BlockRandomize,
): Survey {
  const blocks = survey.blocks ?? [];
  const target = blocks.find((b) => b.id === blockId);
  if (!target) return survey;
  const next: Block = { ...target };
  if (randomize === "none") delete next.randomize;
  else next.randomize = randomize;
  return { ...survey, blocks: blocks.map((b) => (b.id === blockId ? next : b)) };
}

/**
 * Move the block at `index` one slot toward the start (no-op at the top). This is a PURE
 * METADATA reorder of `survey.blocks` — it does NOT reorder `survey.questions` (the flat array
 * stays the flow source of truth), so no logic target can break. Block ids stay stable.
 */
export function moveBlockUp(survey: Survey, index: number): Survey {
  const blocks = survey.blocks ?? [];
  if (index <= 0 || index >= blocks.length) return survey;
  const next = [...blocks];
  [next[index - 1], next[index]] = [next[index], next[index - 1]];
  return { ...survey, blocks: next };
}

/** Move the block at `index` one slot toward the end (no-op at the bottom). Metadata-only. */
export function moveBlockDown(survey: Survey, index: number): Survey {
  const blocks = survey.blocks ?? [];
  if (index < 0 || index >= blocks.length - 1) return survey;
  const next = [...blocks];
  [next[index], next[index + 1]] = [next[index + 1], next[index]];
  return { ...survey, blocks: next };
}

/**
 * Ungroup a block entirely: remove it from `survey.blocks`. Its member questions STAY in
 * `survey.questions` (they were never owned by the block) — they simply become ungrouped.
 * NO-OPS when the id isn't found.
 */
export function ungroupBlock(survey: Survey, blockId: string): Survey {
  const blocks = survey.blocks ?? [];
  if (!blocks.some((b) => b.id === blockId)) return survey;
  return { ...survey, blocks: blocks.filter((b) => b.id !== blockId) };
}

/** The question ids NOT in any block — eligible to be added to a block. Flat-order preserving. */
export function ungroupedQuestionIds(survey: Survey): string[] {
  const claimed = claimedQuestionIds(survey);
  return survey.questions.map((q) => q.id).filter((id) => !claimed.has(id));
}

// --------------------------------------------------------------------------- //
//  PUT payload (openapi SurveyWrite envelope)                                 //
// --------------------------------------------------------------------------- //
/**
 * Build the SurveyWrite body for PUT /surveys/{id}. The deep blobs
 * (questions/languages/styling/welcome_card/endings) are passed through opaquely;
 * the server re-validates them against survey-schema.json.
 *
 * `environment_id` is immutable on PUT (the server ignores it — see
 * apps/surveys/serializers.py); we forward it only if the loaded survey carried
 * it, so the body round-trips cleanly. The builder does NOT depend on having it.
 */
export function toSurveyWrite(survey: Survey): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: survey.name ?? "Untitled survey",
    questions: survey.questions ?? [],
    languages: survey.languages ?? [],
  };
  if (survey.styling !== undefined) body.styling = survey.styling;
  if (survey.welcomeCard !== undefined) body.welcome_card = survey.welcomeCard;
  if (survey.endings !== undefined) body.endings = survey.endings;
  // Survey-level hidden fields: forward the camelCase definition shape under the snake_case
  // body key the SurveyWrite serializer expects (hidden_fields -> hiddenFields downstream).
  if (survey.hiddenFields !== undefined) body.hidden_fields = survey.hiddenFields;
  // Survey follow-ups: forward the camelCase definition shape under the snake_case body key
  // (follow_ups -> followUps downstream, mirroring hidden_fields).
  if (survey.followUps !== undefined) body.follow_ups = survey.followUps;
  // Question-grouping blocks: forward as-is — the key is `blocks` in both the body and the
  // column (no snake/camel rename). Default [] == no blocks == flat behavior unchanged.
  if (survey.blocks !== undefined) body.blocks = survey.blocks;
  // Survey variables: forward as-is — the key is `variables` in both body + column (no rename).
  // Default [] == no variables == behavior unchanged.
  if (survey.variables !== undefined) body.variables = survey.variables;
  // Response quotas: forward as-is — the key is `quotas` in both body + column (no rename).
  // Default [] == no quotas == behavior unchanged.
  if (survey.quotas !== undefined) body.quotas = survey.quotas;
  if (survey.single_use !== undefined) body.single_use = survey.single_use;
  if (survey.capture_ip !== undefined) body.capture_ip = survey.capture_ip;
  // close_on (auto-close rules): forward the object, or null to clear. The server's
  // DictField accepts null; omitting it entirely would leave the column unchanged on PUT.
  if (survey.close_on !== undefined) body.close_on = survey.close_on;
  // App-survey fields (survey_type / triggers / display_options — WS-11): forward each ONLY when
  // present. The SurveyWrite serializer treats an OMITTED field as "leave as-is on PUT", so a link
  // survey that never set these stays byte-identical on the wire (no survey_type/triggers/
  // display_options keys), preserving the link-survey contract exactly.
  if (survey.survey_type !== undefined) body.survey_type = survey.survey_type;
  if (survey.triggers !== undefined) body.triggers = survey.triggers;
  if (survey.display_options !== undefined) body.display_options = survey.display_options;
  // environment_id is read-only on PUT; forward only when present on the loaded survey.
  const envId = (survey as unknown as Record<string, unknown>).environment_id;
  if (typeof envId === "string") body.environment_id = envId;
  return body;
}

/**
 * Bridge a server-loaded survey into the builder's renderable shape. The authenticated
 * SurveySerializer emits snake_case columns (`hidden_fields`, `follow_ups`, `welcome_card`);
 * the builder edits camelCase (`hiddenFields`, `followUps`, `welcomeCard`). Without this bridge
 * those fields render empty — on initial load AND on the Save echo (the server response is
 * snake_case too) — and get re-wiped on the next Save. Run it on EVERY survey received from the
 * server (data-initial-survey, GET, and the PUT/save echo). Also applies defensive array defaults.
 */
export function normalizeInitialSurvey(initialSurvey: Survey): Survey {
  return {
    ...initialSurvey,
    questions: Array.isArray(initialSurvey.questions) ? initialSurvey.questions : [],
    languages:
      Array.isArray(initialSurvey.languages) && initialSurvey.languages.length > 0
        ? initialSurvey.languages
        : [{ code: "default", default: true }],
    hiddenFields:
      initialSurvey.hiddenFields ??
      (initialSurvey as { hidden_fields?: HiddenField[] }).hidden_fields ??
      [],
    followUps:
      initialSurvey.followUps ??
      (initialSurvey as { follow_ups?: FollowUp[] }).follow_ups ??
      [],
    welcomeCard:
      initialSurvey.welcomeCard ??
      (initialSurvey as { welcome_card?: WelcomeCard | null }).welcome_card ??
      null,
    variables: Array.isArray(initialSurvey.variables) ? initialSurvey.variables : [],
  };
}
