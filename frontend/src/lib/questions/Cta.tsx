// ============================================================================
// OpenSurvey — statement / call-to-action question.
//
// Displays content (html) + an optional external link button. Not a
// data-collecting question unless `required` (acts as an acknowledged gate).
// Answer shape: boolean (clicked/acknowledged) or null.
//
// The button records the acknowledgement (sets answer=true) AND, if buttonUrl
// is set, opens the link in a new tab. dismissable controls whether a "skip"
// affordance is offered (handled by the runtime's nav, not here).
// ============================================================================
import { resolveI18n } from "../i18n";
import { sanitizeHtml } from "../sanitizeHtml";
import type { CtaConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function Cta({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<CtaConfig, boolean>) {
  const html = resolveI18n(config?.html, lang, defaultLang);
  const buttonLabel = resolveI18n(config?.buttonLabel, lang, defaultLang) || "Continue";
  const url = config?.buttonUrl;
  const acknowledged = value === true;

  function acknowledge() {
    onChange(true);
  }

  return (
    <div className="space-y-4">
      {html && (
        // Author-authored rich text. Although it is server-validated, the stored HTML is
        // author-controlled, so we sanitize it (lib/sanitizeHtml) before render as a hard XSS
        // boundary — script/event-handlers/javascript: URLs are neutralized; safe formatting kept.
        <div
          className="prose prose-slate max-w-none text-base text-slate-700"
          dangerouslySetInnerHTML={{ __html: sanitizeHtml(html) }}
        />
      )}
      <button
        type="button"
        id={inputId}
        onClick={acknowledge}
        {...(url
          ? {
              // Anchor-like behavior without leaving the survey: open in a new tab.
              onClickCapture: () => window.open(url, "_blank", "noopener,noreferrer"),
            }
          : {})}
        className={
          "inline-flex items-center justify-center rounded-lg px-5 py-2.5 text-base font-medium transition " +
          (acknowledged
            ? "bg-indigo-100 text-indigo-700"
            : "bg-indigo-600 text-white hover:bg-indigo-700")
        }
      >
        {buttonLabel}
      </button>
    </div>
  );
}
