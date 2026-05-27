// ============================================================================
// OpenSurvey — Recall / piping resolution (shared runtime + builder preview).
//
// Lets an author embed a PRIOR answer (or a survey-level hidden field, or any
// captured Response.data value) inside a later question's headline/subheader.
//
// FROZEN TOKEN FORMAT (mirrors Formbricks' recall idea, single OpenSurvey shape):
//
//     #recall:<id>/fallback:<text>#
//
//   - <id>       the source key: an EARLIER question id, a hiddenFields[].id, OR a
//                survey variables[].id. Charset matches survey-schema.json #/$defs/questionId
//                (`^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`) — all three share that id namespace.
//                Variable LIVE values are threaded into `answers` by the caller (the runtime
//                via computeVariables; the follow-up send path server-side), exactly like
//                hidden-field values — so resolveRecall reads them by id with no special case.
//   - <text>     the fallback shown when the source is unanswered/empty. It MAY be
//                empty (`#recall:userId/fallback:#`) and MUST NOT contain `#`
//                (the `#` delimiters bound the token; a `#` inside fallback text
//                would end the token early — documented + enforced by the regex).
//
// resolveRecall(text, answers, hiddenFields, lang) replaces every token with the
// live value from `answers` (hidden field values live IN `answers` too — the
// runtime captures them from the URL into the response data map), or the fallback
// when the source is unanswered/empty. Text with NO token is returned UNCHANGED
// (single-survey/no-recall behavior is byte-identical).
//
// VALUE RENDERING (v1, deliberate bound): scalars (string/number/boolean) render
// directly; arrays join with ", "; an "other" choice answer ({choiceId, other})
// renders its typed `other` text; objects (contactInfo/address/matrix) are NOT
// piped (they render as their fallback) — resolving choice LABELS / object shapes
// to display text needs the question definitions, which this helper intentionally
// does not take. Noted as deferred; recall of text/number/select-id/hidden covers
// the common case.
// ============================================================================
import type { AnswerMap, AnswerValue, HiddenField, Json } from "./types";

/**
 * The frozen recall token matcher. Capture groups:
 *   [1] = source id (question id / hidden field id)
 *   [2] = fallback text (may be empty; may not contain '#')
 * Global so a single string can carry multiple tokens.
 */
export const RECALL_TOKEN_RE = /#recall:([a-zA-Z][a-zA-Z0-9_-]{0,63})\/fallback:([^#]*)#/g;

/** A non-global probe (RECALL_TOKEN_RE carries `lastIndex` state; this one is safe to reuse). */
const RECALL_PROBE_RE = /#recall:[a-zA-Z][a-zA-Z0-9_-]{0,63}\/fallback:[^#]*#/;

/** True if `text` contains at least one recall token. */
export function hasRecallToken(text: string | undefined | null): boolean {
  return typeof text === "string" && RECALL_PROBE_RE.test(text);
}

/** True if an answer value is "empty" (mirrors the runtime/engine empty rule). */
function isEmpty(value: AnswerValue | undefined): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0) ||
    (typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0)
  );
}

/**
 * Render a single answer value to display text for piping. Returns `null` when the
 * value cannot be rendered as plain text (object-shaped answers) so the caller uses
 * the fallback instead.
 */
function renderValue(value: AnswerValue): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    // [choiceId | url | …] — join the scalar members; drop non-scalars.
    const parts = value
      .filter((v) => typeof v === "string" || typeof v === "number")
      .map((v) => String(v));
    return parts.length > 0 ? parts.join(", ") : null;
  }
  if (typeof value === "object") {
    // An "Other" choice answer carries the typed text; pipe that.
    const obj = value as Record<string, Json>;
    if (typeof obj.other === "string" && obj.other !== "") return obj.other;
    // contactInfo / address / matrix / scheduleMeeting object shapes are not piped (v1).
    return null;
  }
  return null;
}

/**
 * Resolve recall tokens in `text` against the answers/hidden-field values.
 *
 * @param text          the headline/subheader string (already i18n-resolved).
 * @param answers       Response.data so far — includes captured hidden-field values.
 * @param _hiddenFields declared hidden fields (informational; values live in `answers`).
 *                      Accepted for signature symmetry + future label use; not required
 *                      for resolution because the runtime captures hidden values INTO
 *                      `answers` under the same id.
 * @param _lang         active language (reserved; recall values are language-agnostic in v1).
 * @returns the text with every token replaced by its live value or fallback. Text with
 *          no token is returned unchanged.
 */
export function resolveRecall(
  text: string | undefined | null,
  answers: AnswerMap | undefined,
  _hiddenFields?: HiddenField[],
  _lang?: string,
): string {
  if (typeof text !== "string" || text === "") return text ?? "";
  if (!RECALL_PROBE_RE.test(text)) return text; // no token → unchanged (fast path)

  const map = answers ?? {};
  return text.replace(RECALL_TOKEN_RE, (_match, id: string, fallback: string) => {
    const value = map[id];
    if (isEmpty(value)) return fallback;
    const rendered = renderValue(value);
    return rendered !== null && rendered !== "" ? rendered : fallback;
  });
}
