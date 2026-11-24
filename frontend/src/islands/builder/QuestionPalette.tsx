// ============================================================================
// onForm — Builder question palette.
//
// The "add a question" menu: one tile per question type the runtime can render
// (openText covers short+long via config.inputType). Clicking a tile appends a
// schema-valid default question (see surveyModel.makeQuestion) and selects it
// for editing.
// ============================================================================
import type { CoreQuestionType, PaletteEntry } from "./types";

/** Catalogue of the authorable types shown in the palette (order = display order). */
export const PALETTE: PaletteEntry[] = [
  { type: "openText", label: "Text", hint: "Short or long free text", glyph: "✍️" },
  { type: "singleSelect", label: "Single select", hint: "Pick one (radios / dropdown)", glyph: "◉" },
  { type: "multiSelect", label: "Multi select", hint: "Pick many (checkboxes)", glyph: "☑" },
  { type: "pictureChoice", label: "Picture Selection", hint: "Pick one or many images", glyph: "🖼" },
  { type: "rating", label: "Rating", hint: "Stars / numbers / smileys", glyph: "★" },
  { type: "nps", label: "NPS", hint: "0–10 recommendation score", glyph: "📊" },
  { type: "cta", label: "Statement / CTA", hint: "Message + optional button", glyph: "📣" },
  { type: "consent", label: "Consent", hint: "Acknowledgement checkbox", glyph: "✔" },
  { type: "ranking", label: "Ranking", hint: "Order a list of options", glyph: "↕" },
  { type: "matrix", label: "Matrix", hint: "Rows × columns grid", glyph: "▦" },
  { type: "date", label: "Date", hint: "Date picker", glyph: "📅" },
  { type: "number", label: "Number", hint: "Numeric input", glyph: "#" },
  { type: "contactInfo", label: "Contact info", hint: "Name / email / phone / company", glyph: "👤" },
  { type: "address", label: "Address", hint: "Street / city / state / zip / country", glyph: "🏠" },
  { type: "scheduleMeeting", label: "Schedule a meeting", hint: "Cal.com inline booking", glyph: "🗓" },
  { type: "fileUpload", label: "File upload", hint: "Attach a file", glyph: "📎" },
];

export function QuestionPalette({ onAdd }: { onAdd: (type: CoreQuestionType) => void }) {
  return (
    <div>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Add question
      </h2>
      <div className="grid grid-cols-2 gap-2">
        {PALETTE.map((entry) => (
          <button
            key={entry.type}
            type="button"
            onClick={() => onAdd(entry.type)}
            title={entry.hint}
            className="flex items-start gap-2 rounded-lg border border-hairline bg-white px-3 py-2 text-left transition-colors hover:border-brand hover:bg-brand/5"
          >
            <span aria-hidden="true" className="text-base leading-5">
              {entry.glyph}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium text-ink">{entry.label}</span>
              <span className="block truncate text-xs text-muted">{entry.hint}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
