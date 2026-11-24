// ============================================================================
// onForm — shared HTML sanitizer for author-formatted rich text.
//
// Rich-text question DESCRIPTIONS (Question.descriptionHtml) are authored as a
// small subset of HTML in the builder and rendered with React's
// `dangerouslySetInnerHTML` at runtime + preview. That makes sanitization a
// HARD security boundary: the stored HTML is author-controlled, but a malicious
// author (or a tampered survey definition) must never be able to inject script,
// event handlers, or dangerous URL schemes into a respondent's browser.
//
// This wraps DOMPurify with a TIGHT allowlist:
//   - Tags: b, strong, i, em, u, a, br, p, ul, ol, li, span
//   - Attributes: href (only on <a>) + the target/rel we force on links below.
//   - URL schemes on href: http(s) and mailto only. javascript:/data:/vbscript:
//     (and any other scheme) are stripped by DOMPurify's default URI policy.
//   - All event-handler attributes (onclick, onerror, …) are dropped (they are
//     simply not in ALLOWED_ATTR, and DOMPurify removes them).
//   - Every surviving <a> is hardened to target="_blank" rel="noopener
//     noreferrer nofollow" so links can't reach window.opener or leak referrer.
//
// ORDERING CONTRACT (see lib/recall.ts + QuestionRenderer): callers resolve
// i18n THEN recall/piping tokens FIRST, and sanitize the RESULT. Sanitizing
// before recall would let a respondent's prior answer (piped in via a recall
// token) smuggle markup into already-trusted HTML; sanitizing LAST guarantees
// any HTML coming from answers/hidden-fields is neutralized too.
// ============================================================================
import DOMPurify from "dompurify";

/** Inline + minimal block tags an author may use in a rich-text description. */
export const ALLOWED_TAGS = [
  "b",
  "strong",
  "i",
  "em",
  "u",
  "a",
  "br",
  "p",
  "ul",
  "ol",
  "li",
  "span",
] as const;

/**
 * Attributes kept on surviving elements. `href` is the only author-settable one
 * (links). `target`/`rel` are added by us in the hook below, so they must be in
 * the allowlist or DOMPurify would strip the values we just set.
 */
export const ALLOWED_ATTR = ["href", "target", "rel"] as const;

// Harden every anchor that survives sanitization. Registered ONCE at module
// load (DOMPurify hooks are global to the singleton). The hook runs per-node
// AFTER attribute sanitization, so a surviving <a> already has a safe href.
let hookRegistered = false;
function ensureAnchorHook(): void {
  if (hookRegistered) return;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    // Only anchors carry target/rel; guard with tagName so we don't touch others.
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer nofollow");
    }
  });
  hookRegistered = true;
}

/**
 * Sanitize author rich text to the safe subset above. Returns a string of HTML
 * safe to pass to `dangerouslySetInnerHTML`. Empty/blank input → "".
 *
 * SAFETY NOTES:
 *   - `<script>`, `<style>`, `<iframe>`, etc. are removed (not in ALLOWED_TAGS;
 *     RETURN_TRUSTED_TYPE off, so we get a string back).
 *   - `onerror=`/`onclick=` and all event handlers are stripped (not allowed).
 *   - `href="javascript:…"` / `data:` / `vbscript:` are stripped by DOMPurify's
 *     default ALLOWED_URI_REGEXP (we keep the default policy, which permits only
 *     http/https/ftp/mailto/tel/callto/sms/cid/xmpp + relative/anchor URIs).
 *   - Plain text with NO markup passes through with HTML entities preserved, so
 *     a non-formatted description renders identically to plain text would.
 */
export function sanitizeHtml(dirty: string | undefined | null): string {
  if (typeof dirty !== "string" || dirty === "") return "";
  ensureAnchorHook();
  return DOMPurify.sanitize(dirty, {
    ALLOWED_TAGS: [...ALLOWED_TAGS],
    ALLOWED_ATTR: [...ALLOWED_ATTR],
    // Keep DOMPurify's safe default URI policy (blocks javascript:/data:/vbscript:).
    ALLOW_DATA_ATTR: false,
    // Return a plain string (not a TrustedHTML object) for dangerouslySetInnerHTML.
    RETURN_TRUSTED_TYPE: false,
  });
}

/**
 * True if `html` contains any rendered text/markup after sanitization — i.e. it
 * is worth rendering as an HTML description rather than falling back to the
 * plain-text subheader. Whitespace-only / empty-tag-only HTML is treated as
 * empty so an author who clears the editor doesn't leave a stray empty block.
 */
export function hasRichText(html: string | undefined | null): boolean {
  if (typeof html !== "string" || html === "") return false;
  const clean = sanitizeHtml(html);
  // Strip tags + entities to test for actual visible content.
  const text = clean
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .trim();
  return text !== "";
}
