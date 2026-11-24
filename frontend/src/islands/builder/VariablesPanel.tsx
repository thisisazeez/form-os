// ============================================================================
// onForm — Builder Variables editor (Settings tab section).
//
// Survey variables (survey-schema.json #/$defs/variable) are author-defined,
// typed mutable values (number | text) seeded from an `initialValue`. A
// `calculate` logic action mutates them as the respondent progresses; recall/
// piping (#recall:<id>/...#) and logic conditions can READ them. This panel lets
// the author DEFINE / REMOVE variables (id + name + type + initial value).
//
// Each variable id must match the question-id charset (^[a-zA-Z][a-zA-Z0-9_-]{0,63}$)
// since it shares the recall + condition id namespace; it must not collide with a
// question / ending / hidden-field / variable id (addVariable guards them all). The
// panel emits a FRESH Survey via the surveyModel helpers, so BuilderApp's
// onSurveyChange (dirty-tracking + Save) drives persistence (toSurveyWrite forwards
// it as `variables`).
// ============================================================================
import { useState } from "react";

import type { Survey, Variable } from "../../lib/types";
import {
  addVariable,
  isValidVariableId,
  removeVariable,
  setVariableInitialValue,
  setVariableName,
} from "./surveyModel";

export interface VariablesPanelProps {
  survey: Survey;
  onChange: (next: Survey) => void;
}

export function VariablesPanel({ survey, onChange }: VariablesPanelProps) {
  const variables: Variable[] = survey.variables ?? [];
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<"number" | "text">("number");
  const [error, setError] = useState<string | null>(null);

  const takenIds = new Set<string>([
    ...survey.questions.map((q) => q.id),
    ...(survey.endings ?? []).map((e) => e.id),
    ...(survey.hiddenFields ?? []).map((f) => f.id),
    ...variables.map((v) => v.id),
  ]);

  function submitAdd() {
    const id = newId.trim();
    if (id === "") {
      setError("Enter a variable id.");
      return;
    }
    if (!isValidVariableId(id)) {
      setError("Id must start with a letter and use only letters, numbers, _ or -.");
      return;
    }
    if (takenIds.has(id)) {
      setError(`"${id}" collides with a question, ending, hidden field, or variable id.`);
      return;
    }
    onChange(addVariable(survey, id, newName, newType));
    setNewId("");
    setNewName("");
    setNewType("number");
    setError(null);
  }

  return (
    <section data-testid="variables-panel">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Variables</h3>
      <p className="mb-3 text-xs text-muted/80">
        Define typed values your survey logic can <em>calculate</em> with as respondents progress
        (e.g. a running score). Variable values are available for recall/piping and logic
        conditions.
      </p>

      {/* Declared variable rows */}
      <div className="space-y-1.5">
        {variables.length === 0 ? (
          <span className="text-xs text-muted/70">No variables.</span>
        ) : (
          variables.map((v) => (
            <div
              key={v.id}
              data-testid={`variable-row-${v.id}`}
              className="flex flex-wrap items-center gap-2 rounded-lg bg-panel px-2.5 py-1.5"
            >
              <code className="text-ink">{v.id}</code>
              <span className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-muted">
                {v.type}
              </span>
              <input
                type="text"
                value={v.name}
                aria-label={`Name for variable ${v.id}`}
                onChange={(e) => onChange(setVariableName(survey, v.id, e.target.value))}
                className="w-36 rounded-md border border-hairline bg-white px-2 py-1 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
              />
              <label className="flex items-center gap-1 text-[11px] text-muted/80">
                <span>start</span>
                {v.type === "number" ? (
                  <input
                    type="number"
                    value={typeof v.initialValue === "number" ? v.initialValue : 0}
                    aria-label={`Initial value for variable ${v.id}`}
                    onChange={(e) => onChange(setVariableInitialValue(survey, v.id, e.target.value))}
                    className="w-20 rounded-md border border-hairline bg-white px-2 py-1 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                  />
                ) : (
                  <input
                    type="text"
                    value={typeof v.initialValue === "string" ? v.initialValue : ""}
                    aria-label={`Initial value for variable ${v.id}`}
                    onChange={(e) => onChange(setVariableInitialValue(survey, v.id, e.target.value))}
                    className="w-28 rounded-md border border-hairline bg-white px-2 py-1 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                  />
                )}
              </label>
              <button
                type="button"
                onClick={() => onChange(removeVariable(survey, v.id))}
                aria-label={`Remove variable ${v.id}`}
                title={`Remove "${v.id}"`}
                className="ml-auto rounded p-0.5 text-muted hover:bg-rose-50 hover:text-rose-600"
              >
                ✕
              </button>
            </div>
          ))
        )}
      </div>

      {/* Add-variable form */}
      <div className="mt-2.5 flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="mb-0.5 block text-[11px] font-medium text-muted/80">Id</span>
          <input
            type="text"
            value={newId}
            onChange={(e) => {
              setNewId(e.target.value);
              if (error) setError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitAdd();
              }
            }}
            placeholder="score"
            aria-label="New variable id"
            className="w-32 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </label>
        <label className="block">
          <span className="mb-0.5 block text-[11px] font-medium text-muted/80">
            Name <span className="font-normal text-muted/60">(optional)</span>
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
            placeholder="Score"
            aria-label="New variable name"
            className="w-40 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </label>
        <label className="block">
          <span className="mb-0.5 block text-[11px] font-medium text-muted/80">Type</span>
          <select
            value={newType}
            onChange={(e) => setNewType(e.target.value as "number" | "text")}
            aria-label="New variable type"
            className="rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          >
            <option value="number">number</option>
            <option value="text">text</option>
          </select>
        </label>
        <button
          type="button"
          onClick={submitAdd}
          className="rounded-lg border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand transition-colors hover:border-brand hover:bg-brand/5"
        >
          + Add variable
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
