// ============================================================================
// OpenSurvey — consent question (single acknowledgement checkbox).
// Answer shape: boolean (true = accepted).
// ============================================================================
import { resolveI18n } from "../i18n";
import { sanitizeHtml } from "../sanitizeHtml";
import type { ConsentConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function Consent({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<ConsentConfig, boolean>) {
  const html = resolveI18n(config?.html, lang, defaultLang);
  const label = resolveI18n(config?.label, lang, defaultLang);
  const checked = value === true;

  return (
    <div className="space-y-3">
      {html && (
        // Author-controlled consent copy: server-validated but still author HTML, so we sanitize
        // it (lib/sanitizeHtml) before render — a hard XSS boundary, not cosmetic. Safe inline
        // formatting + links survive; script/handlers/javascript: URLs are stripped.
        <div
          className="prose prose-slate max-w-none text-sm text-slate-600"
          dangerouslySetInnerHTML={{ __html: sanitizeHtml(html) }}
        />
      )}
      <label className="flex cursor-pointer items-start gap-3 text-base text-slate-900">
        <input
          type="checkbox"
          id={inputId}
          checked={checked}
          onChange={(e) => onChange(e.target.checked ? true : undefined)}
          className="mt-1 h-4 w-4 accent-indigo-600"
        />
        <span>{label}</span>
      </label>
    </div>
  );
}
