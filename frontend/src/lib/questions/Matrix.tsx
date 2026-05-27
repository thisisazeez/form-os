// ============================================================================
// OpenSurvey — matrix question (rows × columns grid of choices).
//
// Answer shape (survey-schema.json #/$defs/answerShapes):
//   selection: "single"   -> { rowId: columnId }
//   selection: "multiple" -> { rowId: [columnId] }
//
// Each ROW is its own single-choice (radios) or multi-choice (checkboxes) group
// across the COLUMNS. The answer is an object keyed by rowId; rows with no
// selection are absent from the object. When the object empties we clear to
// undefined so the engine treats the question as unanswered.
// ============================================================================
import { resolveI18n } from "../i18n";
import type { MatrixConfig } from "../types";
import type { QuestionRendererProps } from "./types";

type MatrixValue = Record<string, string | string[]>;

export function Matrix({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<MatrixConfig, MatrixValue>) {
  const rows = config.rows ?? [];
  const columns = config.columns ?? [];
  const multiple = config.selection === "multiple";
  const current: MatrixValue = value && typeof value === "object" ? (value as MatrixValue) : {};

  function commit(next: MatrixValue) {
    onChange(Object.keys(next).length === 0 ? undefined : next);
  }

  function pickSingle(rowId: string, columnId: string) {
    const next = { ...current };
    if (next[rowId] === columnId) {
      delete next[rowId]; // toggle off
    } else {
      next[rowId] = columnId;
    }
    commit(next);
  }

  function toggleMulti(rowId: string, columnId: string) {
    const next = { ...current };
    const prev = Array.isArray(next[rowId]) ? (next[rowId] as string[]) : [];
    const on = prev.includes(columnId);
    const updated = on ? prev.filter((c) => c !== columnId) : [...prev, columnId];
    if (updated.length === 0) {
      delete next[rowId];
    } else {
      next[rowId] = updated;
    }
    commit(next);
  }

  function isChecked(rowId: string, columnId: string): boolean {
    const cell = current[rowId];
    if (multiple) return Array.isArray(cell) && cell.includes(columnId);
    return cell === columnId;
  }

  return (
    <div className="overflow-x-auto">
      <table id={inputId} className="w-full border-collapse text-sm">
        <thead>
          <tr>
            <th className="px-3 py-2 text-left font-medium text-slate-500" />
            {columns.map((col) => (
              <th key={col.id} scope="col" className="px-3 py-2 text-center font-medium text-slate-700">
                {resolveI18n(col.label, lang, defaultLang)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const rowLabel = resolveI18n(row.label, lang, defaultLang);
            return (
              <tr
                key={row.id}
                role={multiple ? "group" : "radiogroup"}
                aria-label={rowLabel}
                className="border-t border-slate-200"
              >
                <th scope="row" className="px-3 py-2 text-left font-normal text-slate-900">
                  {rowLabel}
                </th>
                {columns.map((col) => {
                  const checked = isChecked(row.id, col.id);
                  return (
                    <td key={col.id} className="px-3 py-2 text-center">
                      <input
                        type={multiple ? "checkbox" : "radio"}
                        name={multiple ? undefined : `${inputId}-${row.id}`}
                        checked={checked}
                        aria-label={`${rowLabel} — ${resolveI18n(col.label, lang, defaultLang)}`}
                        onChange={() =>
                          multiple ? toggleMulti(row.id, col.id) : pickSingle(row.id, col.id)
                        }
                        className="h-4 w-4 accent-indigo-600"
                      />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
