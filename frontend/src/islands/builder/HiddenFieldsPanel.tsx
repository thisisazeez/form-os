// ============================================================================
// OpenSurvey — Builder Hidden Fields editor (Settings tab section).
//
// Survey-level hidden fields (survey-schema.json #/$defs/hiddenFields) are
// captured from the URL query string at runtime (?key=value) into the response
// data, where they become available to recall/piping + logic. This panel lets the
// author declare them by key (id) + an optional human label (name).
//
// Each entry's id must match the question-id charset (^[a-zA-Z][a-zA-Z0-9_-]{0,63}$)
// since it shares the recall + Response.data namespace; it must not collide with a
// question/ending id (addHiddenField guards both). The panel emits a FRESH Survey via
// the surveyModel helpers, so BuilderApp's onSurveyChange (dirty-tracking + Save)
// drives persistence (toSurveyWrite forwards it as hidden_fields).
// ============================================================================
import { useState } from "react";

import type { HiddenField, Survey } from "../../lib/types";
import { addHiddenField, isValidHiddenFieldId, removeHiddenField } from "./surveyModel";

export interface HiddenFieldsPanelProps {
  survey: Survey;
  onChange: (next: Survey) => void;
}

export function HiddenFieldsPanel({ survey, onChange }: HiddenFieldsPanelProps) {
  const fields: HiddenField[] = survey.hiddenFields ?? [];
  const [newKey, setNewKey] = useState("");
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const takenIds = new Set<string>([
    ...survey.questions.map((q) => q.id),
    ...(survey.endings ?? []).map((e) => e.id),
    ...fields.map((f) => f.id),
  ]);

  function submitAdd() {
    const key = newKey.trim();
    if (key === "") {
      setError("Enter a field key.");
      return;
    }
    if (!isValidHiddenFieldId(key)) {
      setError("Key must start with a letter and use only letters, numbers, _ or -.");
      return;
    }
    if (fields.some((f) => f.id === key)) {
      setError(`"${key}" is already added.`);
      return;
    }
    if (takenIds.has(key)) {
      setError(`"${key}" collides with a question or ending id.`);
      return;
    }
    onChange(addHiddenField(survey, key, newName));
    setNewKey("");
    setNewName("");
    setError(null);
  }

  return (
    <section data-testid="hidden-fields-panel">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Hidden fields
      </h3>
      <p className="mb-3 text-xs text-muted/80">
        Capture URL parameters (e.g. <code>?userId=123&amp;plan=pro</code>) into each response.
        Hidden field values are available for recall/piping and logic.
      </p>

      {/* Declared hidden-field chips */}
      <div className="flex flex-wrap gap-1.5">
        {fields.length === 0 ? (
          <span className="text-xs text-muted/70">No hidden fields.</span>
        ) : (
          fields.map((f) => (
            <span
              key={f.id}
              className="inline-flex items-center gap-1.5 rounded-full bg-panel py-0.5 pl-2.5 pr-1.5 text-xs font-medium text-muted"
            >
              <code className="text-ink">{f.id}</code>
              {f.name?.trim() ? <span className="text-muted/80">· {f.name}</span> : null}
              <button
                type="button"
                onClick={() => onChange(removeHiddenField(survey, f.id))}
                aria-label={`Remove hidden field ${f.id}`}
                title={`Remove "${f.id}"`}
                className="rounded p-0.5 text-muted hover:bg-rose-50 hover:text-rose-600"
              >
                ✕
              </button>
            </span>
          ))
        )}
      </div>

      {/* Add-field form */}
      <div className="mt-2.5 flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="mb-0.5 block text-[11px] font-medium text-muted/80">Key</span>
          <input
            type="text"
            value={newKey}
            onChange={(e) => {
              setNewKey(e.target.value);
              if (error) setError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitAdd();
              }
            }}
            placeholder="userId"
            aria-label="New hidden field key"
            className="w-32 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </label>
        <label className="block">
          <span className="mb-0.5 block text-[11px] font-medium text-muted/80">
            Label <span className="font-normal text-muted/60">(optional)</span>
          </span>
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitAdd();
              }
            }}
            placeholder="User ID"
            aria-label="New hidden field label"
            className="w-40 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </label>
        <button
          type="button"
          onClick={submitAdd}
          className="rounded-lg border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand transition-colors hover:border-brand hover:bg-brand/5"
        >
          + Add hidden field
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-xs font-medium text-rose-600">
          {error}
        </p>
      )}
    </section>
  );
}
