// ============================================================================
// onForm — Builder survey-level settings (name + language management).
//
// Surfaces the survey name (editable) and the MULTI-LANGUAGE controls:
//   - Add a language (code + optional label, e.g. fr / Français)
//   - Remove a language (never the default; never the last one)
//   - Mark a language as the default (exactly one default, always present)
//   - An authoring-language SWITCHER (shown only when >1 language is configured)
//     that drives which language the QuestionEditor edits and the PreviewPane
//     renders. The default language is the i18nString `default`-key fallback.
//
// Single-language surveys keep the original chip + name UI untouched (the
// switcher and translation affordances stay hidden until a 2nd language exists).
// ============================================================================
import { useState } from "react";

import { defaultLanguageCode } from "../../lib/i18n";
import type { Language, Survey } from "../../lib/types";

export interface SettingsPanelProps {
  survey: Survey;
  onNameChange: (name: string) => void;
  /** The language currently being authored (drives the editor + preview). */
  activeLang: string;
  /** Switch the authoring language. */
  onActiveLangChange: (code: string) => void;
  /** Add a declared language (code + optional label). */
  onAddLanguage: (code: string, label: string) => void;
  /** Remove a declared language by code (guards refuse the default / last one). */
  onRemoveLanguage: (code: string) => void;
  /** Mark a language as the survey default. */
  onSetDefaultLanguage: (code: string) => void;
}

/** A human-friendly display name for a language chip (label, else the code). */
function langDisplay(l: Language): string {
  return l.label?.trim() ? l.label : l.code;
}

export function SettingsPanel({
  survey,
  onNameChange,
  activeLang,
  onActiveLangChange,
  onAddLanguage,
  onRemoveLanguage,
  onSetDefaultLanguage,
}: SettingsPanelProps) {
  const languages: Language[] = survey.languages ?? [];
  const defaultLang = defaultLanguageCode(survey.languages);
  const multi = languages.length > 1;

  const [newCode, setNewCode] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  function submitAdd() {
    const code = newCode.trim();
    if (code === "") {
      setAddError("Enter a language code.");
      return;
    }
    if (languages.some((l) => l.code === code)) {
      setAddError(`"${code}" is already added.`);
      return;
    }
    onAddLanguage(code, newLabel);
    setNewCode("");
    setNewLabel("");
    setAddError(null);
  }

  return (
    <div className="space-y-4">
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-muted">Survey name</span>
        <input
          type="text"
          value={survey.name ?? ""}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="Untitled survey"
          className="w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
        />
      </label>

      <div>
        <span className="mb-1 block text-xs font-medium text-muted">Languages</span>

        {/* Language chips with default badge + actions */}
        <div className="flex flex-wrap gap-1.5">
          {languages.length === 0 ? (
            <span className="text-xs text-muted/70">No languages declared.</span>
          ) : (
            languages.map((l) => {
              const isDefault = l.default;
              return (
                <span
                  key={l.code}
                  className={
                    "inline-flex items-center gap-1.5 rounded-full py-0.5 pl-2.5 pr-1.5 text-xs font-medium " +
                    (isDefault ? "bg-brand/10 text-brand" : "bg-panel text-muted")
                  }
                >
                  <span>{langDisplay(l)}</span>
                  {isDefault ? (
                    <span className="text-[10px] uppercase tracking-wide">default</span>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => onSetDefaultLanguage(l.code)}
                        title={`Make "${langDisplay(l)}" the default language`}
                        className="rounded px-1 text-[10px] uppercase tracking-wide text-muted hover:text-brand"
                      >
                        set default
                      </button>
                      <button
                        type="button"
                        onClick={() => onRemoveLanguage(l.code)}
                        aria-label={`Remove ${langDisplay(l)}`}
                        title={`Remove "${langDisplay(l)}"`}
                        className="rounded p-0.5 text-muted hover:bg-rose-50 hover:text-rose-600"
                      >
                        ✕
                      </button>
                    </>
                  )}
                </span>
              );
            })
          )}
        </div>

        {/* Add-language form */}
        <div className="mt-2.5 flex flex-wrap items-end gap-2">
          <label className="block">
            <span className="mb-0.5 block text-[11px] font-medium text-muted/80">Code</span>
            <input
              type="text"
              value={newCode}
              onChange={(e) => {
                setNewCode(e.target.value);
                if (addError) setAddError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submitAdd();
                }
              }}
              placeholder="fr"
              aria-label="New language code"
              className="w-20 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
            />
          </label>
          <label className="block">
            <span className="mb-0.5 block text-[11px] font-medium text-muted/80">
              Label <span className="font-normal text-muted/60">(optional)</span>
            </span>
            <input
              type="text"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submitAdd();
                }
              }}
              placeholder="Français"
              aria-label="New language label"
              className="w-32 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
            />
          </label>
          <button
            type="button"
            onClick={submitAdd}
            className="rounded-lg border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand transition-colors hover:border-brand hover:bg-brand/5"
          >
            + Add language
          </button>
        </div>
        {addError && (
          <p role="alert" className="mt-1 text-xs font-medium text-rose-600">
            {addError}
          </p>
        )}
      </div>

      {/* Authoring-language switcher — only meaningful with >1 language */}
      {multi && (
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">
            Editing language{" "}
            <span className="font-normal text-muted/70">
              (translate each field; blank falls back to {defaultLang})
            </span>
          </span>
          <select
            value={activeLang}
            onChange={(e) => onActiveLangChange(e.target.value)}
            aria-label="Editing language"
            className="w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          >
            {languages.map((l) => (
              <option key={l.code} value={l.code}>
                {langDisplay(l)}
                {l.default ? " (default)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}
