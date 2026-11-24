// ============================================================================
// onForm — answer completeness helpers (runtime gating).
//
// The logic engine decides NAVIGATION; these helpers decide whether the current
// question's answer is COMPLETE enough to advance (required-gating + per-type
// constraints like multiSelect min/max). Emptiness matches the engine's
// `isEmptyValue` (README §3): "", [], {}, null/undefined are empty; 0 and false
// are NOT empty.
// ============================================================================
import type {
  AddressConfig,
  AddressFieldKey,
  AnswerValue,
  ContactFieldKey,
  ContactInfoConfig,
  DateConfig,
  MatrixConfig,
  MultiSelectConfig,
  NumberConfig,
  OpenTextConfig,
  Question,
} from "./types";

/** Contact sub-fields in display order (mirrors ContactInfo.tsx). */
const CONTACT_FIELDS: ContactFieldKey[] = ["firstName", "lastName", "email", "phone", "company"];

/** Address sub-fields in display order (mirrors Address.tsx). */
const ADDRESS_FIELDS: AddressFieldKey[] = [
  "addressLine1",
  "addressLine2",
  "city",
  "state",
  "zip",
  "country",
];

/** True if the value is "empty" per the frozen evaluator semantics (README §3). */
export function isEmptyAnswer(value: AnswerValue | undefined): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value === "";
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") {
    // {choiceId, other}: complete iff a choice id is set (other text may be blank
    // unless the question requires it — caller enforces). Generic object: non-empty
    // iff it has keys.
    if ("choiceId" in value) return !value.choiceId;
    return Object.keys(value).length === 0;
  }
  return false; // numbers (incl. 0), booleans (incl. false)
}

/**
 * Validate the current answer for a question against its required-ness and
 * per-type constraints. Returns null if OK, or a short user-facing message.
 *
 * `required` is the EFFECTIVE requiredness for this respondent (static OR
 * logic-required), computed by the engine's requiredQuestionIds.
 */
export function validateAnswer(
  question: Question,
  value: AnswerValue | undefined,
  required: boolean,
): string | null {
  const empty = isEmptyAnswer(value);

  if (required && empty) {
    return "This question is required.";
  }
  // Optional + empty: nothing more to check (skipping is allowed).
  if (empty) return null;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const config = (question.config ?? {}) as any;

  if (question.type === "openText") {
    const cfg = config as OpenTextConfig;
    const text = typeof value === "string" ? value : "";
    if (cfg.minLength !== undefined && text.length < cfg.minLength) {
      return `Please enter at least ${cfg.minLength} characters.`;
    }
  }

  if (question.type === "multiSelect") {
    const cfg = config as MultiSelectConfig;
    const arr = Array.isArray(value) ? value : [];
    if (cfg.minSelections !== undefined && arr.length < cfg.minSelections) {
      return `Please select at least ${cfg.minSelections}.`;
    }
    if (cfg.maxSelections !== undefined && arr.length > cfg.maxSelections) {
      return `Please select at most ${cfg.maxSelections}.`;
    }
  }

  // "Other" choice with required text: if the chosen choice isOther and required,
  // the other text must be present.
  if (
    question.type === "singleSelect" &&
    value !== null &&
    typeof value === "object" &&
    "choiceId" in value &&
    required &&
    !value.other?.trim()
  ) {
    return "Please specify your answer.";
  }

  if (question.type === "number") {
    const cfg = config as NumberConfig;
    const n = typeof value === "number" ? value : NaN;
    if (Number.isNaN(n)) return "Please enter a number.";
    if (cfg.min !== undefined && n < cfg.min) return `Please enter ${cfg.min} or more.`;
    if (cfg.max !== undefined && n > cfg.max) return `Please enter ${cfg.max} or less.`;
  }

  if (question.type === "date") {
    const cfg = config as DateConfig;
    const d = typeof value === "string" ? value : "";
    // ISO 'YYYY-MM-DD' compares lexicographically in date order.
    if (cfg.min !== undefined && d < cfg.min) return `Please pick a date on or after ${cfg.min}.`;
    if (cfg.max !== undefined && d > cfg.max) return `Please pick a date on or before ${cfg.max}.`;
  }

  if (question.type === "contactInfo") {
    const cfg = config as ContactInfoConfig;
    const fields = cfg.fields ?? {};
    const answer = (value && typeof value === "object" ? value : {}) as Record<string, string>;
    for (const key of CONTACT_FIELDS) {
      const sub = fields[key];
      if (!sub || sub.show === false) continue;
      const filled = typeof answer[key] === "string" && answer[key].trim() !== "";
      if (sub.required && !filled) return "Please complete the required contact fields.";
      // Lightweight email sanity check (the input is type=email; this guards programmatic edits).
      if (key === "email" && filled && !answer.email.includes("@")) {
        return "Please enter a valid email address.";
      }
    }
  }

  if (question.type === "matrix") {
    const cfg = config as MatrixConfig;
    const rows = cfg.rows ?? [];
    const answer = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
    // When the matrix is required, every row must carry a selection.
    if (required) {
      const allAnswered = rows.every((r) => {
        const cell = answer[r.id];
        if (Array.isArray(cell)) return cell.length > 0;
        return typeof cell === "string" && cell !== "";
      });
      if (!allAnswered) return "Please answer every row.";
    }
  }

  if (question.type === "address") {
    const cfg = config as AddressConfig;
    const fields = cfg.fields ?? {};
    const answer = (value && typeof value === "object" ? value : {}) as Record<string, string>;
    for (const key of ADDRESS_FIELDS) {
      const sub = fields[key];
      if (!sub || sub.show === false) continue;
      const filled = typeof answer[key] === "string" && answer[key].trim() !== "";
      if (sub.required && !filled) return "Please complete the required address fields.";
    }
  }

  // pictureChoice: the answer is a choiceId (single) or [choiceId] (multi). Both
  // shapes are covered by the emptiness gate above; no extra per-type constraint
  // (the schema/builder constrain the option set, and there is no min/max select).
  // scheduleMeeting: the answer is {booked:true, uid?} once a meeting is booked.
  // The emptiness gate treats the populated object as non-empty, so a required
  // scheduleMeeting is satisfied once a booking is recorded; nothing more to check.
  // ranking: the renderer always produces a full ordering once touched; no extra
  // per-type constraint beyond required-gating (handled above).
  // fileUpload: the upload backend is deferred (FileUpload.tsx) — no URLs are
  // produced, so there is no answer to validate here yet.

  return null;
}
