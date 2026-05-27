// ============================================================================
// OpenSurvey — minimal rich-text editor for question DESCRIPTIONS.
//
// A small contentEditable surface with a bold / italic / underline / link
// toolbar (+ line breaks via Enter). It edits the HTML for the CURRENT authoring
// language and emits sanitized HTML up to the QuestionEditor, which stores it in
// `question.descriptionHtml` (an i18nStringOptional map) — multi-language aware
// via the same setOptionalLangText helper used for plain text.
//
// DESIGN BOUNDS (deliberate, per the task — no heavyweight editor framework):
//   - Uses document.execCommand for formatting. It is deprecated but still the
//     pragmatic, dependency-free way to toggle bold/italic/underline + insert a
//     link in a contentEditable. We do NOT rely on its return value.
//   - The emitted HTML is run through the SAME shared sanitizer the runtime uses
//     (lib/sanitizeHtml) before it leaves the component, so what the author sees
//     stored == what the runtime will render (no surprise stripping later).
//   - Recall tokens (`#recall:…#`) are plain text; the `@ Recall` insert drops a
//     literal token at the caret. The sanitizer leaves the token text intact.
//
// SAFETY: this is the AUTHORING side; the security boundary is the runtime's
// sanitize-on-render. We still sanitize on emit so stored HTML is already clean.
// ============================================================================
import { useEffect, useRef } from "react";

import { sanitizeHtml } from "../../lib/sanitizeHtml";

export interface RichTextEditorProps {
  /** Current HTML for the active language (already i18n-resolved by the caller). */
  value: string;
  /** Emit sanitized HTML whenever the author edits. "" when the editor is cleared. */
  onChange: (html: string) => void;
  placeholder?: string;
  /** Stable id so the toolbar buttons + a11y label associate with this instance. */
  id?: string;
  /**
   * Imperative caret insert hook. The parent wires its `@ Recall` button to call
   * `insertText(token)` — we expose it by accepting a ref-setter so the parent can
   * insert at the live caret. Optional; recall still works by appending if unused.
   */
  onReady?: (api: RichTextEditorApi) => void;
}

export interface RichTextEditorApi {
  /** Insert literal text (e.g. a recall token) at the current caret, then emit. */
  insertText: (text: string) => void;
  /** Focus the editable surface. */
  focus: () => void;
}

/**
 * Execute a formatting command on the current selection inside the editor.
 * Returns false (and no-ops) where execCommand is unavailable (e.g. jsdom in
 * tests) so the component degrades gracefully instead of throwing.
 */
function exec(command: string, value?: string): boolean {
  if (typeof document === "undefined" || typeof document.execCommand !== "function") {
    return false;
  }
  // execCommand is deprecated but remains the simplest cross-browser way to
  // toggle inline formatting in a contentEditable without an editor framework.
  return document.execCommand(command, false, value);
}

export function RichTextEditor({
  value,
  onChange,
  placeholder,
  id,
  onReady,
}: RichTextEditorProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  // The last value WE emitted upward. Lets the sync effect distinguish a value
  // prop that merely echoes our own keystroke (skip — would reset the caret) from
  // a genuine EXTERNAL change (language/question switch, recall append → re-sync).
  const lastEmitted = useRef<string | null>(null);

  // Keep the DOM in sync when the value changes from OUTSIDE WITHOUT clobbering
  // the caret while the author is typing. Skip when the incoming value is the one
  // we just emitted (the DOM already holds equivalent markup).
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (value === lastEmitted.current) return; // our own edit echoed back — leave caret alone
    if (el.innerHTML !== (value ?? "")) {
      el.innerHTML = value ?? "";
    }
    lastEmitted.current = null;
  }, [value]);

  // Emit the editor's sanitized HTML. Empty/blank → "" (clears descriptionHtml).
  function emit(): void {
    const el = ref.current;
    if (!el) return;
    const raw = el.innerHTML;
    const clean = sanitizeHtml(raw);
    // A contentEditable left empty produces "<br>" or "&nbsp;" noise; normalize.
    const stripped = clean
      .replace(/<br\s*\/?>/gi, "")
      .replace(/&nbsp;/gi, "")
      .replace(/<[^>]*>/g, "")
      .trim();
    const out = stripped === "" ? "" : clean;
    lastEmitted.current = out;
    onChange(out);
  }

  // Expose the imperative caret-insert API to the parent (recall insertion).
  useEffect(() => {
    if (!onReady) return;
    const api: RichTextEditorApi = {
      insertText(text: string) {
        const el = ref.current;
        if (!el) return;
        el.focus();
        // Insert at the live caret; execCommand keeps the selection sensible.
        const ok = exec("insertText", text);
        if (!ok) {
          // Fallback (or no execCommand, e.g. jsdom): append at the end.
          el.append(document.createTextNode(text));
        }
        emit();
      },
      focus() {
        ref.current?.focus();
      },
    };
    onReady(api);
    // We intentionally register once; the api closes over the live ref + emit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onLink(): void {
    const url = window.prompt("Link URL (https://…)");
    if (url == null) return;
    const trimmed = url.trim();
    if (trimmed === "") {
      exec("unlink");
    } else {
      exec("createLink", trimmed);
    }
    emit();
  }

  const showPlaceholder = (value ?? "").trim() === "";

  return (
    <div className="rounded-lg border border-hairline bg-white shadow-sm focus-within:border-brand focus-within:ring-1 focus-within:ring-brand">
      <div
        role="toolbar"
        aria-label="Description formatting"
        className="flex items-center gap-1 border-b border-hairline px-2 py-1.5"
      >
        <ToolbarButton label="Bold" title="Bold" onClick={() => { exec("bold"); emit(); }}>
          <span className="font-bold">B</span>
        </ToolbarButton>
        <ToolbarButton label="Italic" title="Italic" onClick={() => { exec("italic"); emit(); }}>
          <span className="italic">I</span>
        </ToolbarButton>
        <ToolbarButton label="Underline" title="Underline" onClick={() => { exec("underline"); emit(); }}>
          <span className="underline">U</span>
        </ToolbarButton>
        <span className="mx-1 h-4 w-px bg-hairline" aria-hidden="true" />
        <ToolbarButton label="Insert link" title="Insert link" onClick={onLink}>
          <span aria-hidden="true">🔗</span>
        </ToolbarButton>
      </div>
      <div className="relative">
        <div
          id={id}
          ref={ref}
          role="textbox"
          aria-multiline="true"
          aria-label="Question description"
          contentEditable
          suppressContentEditableWarning
          onInput={emit}
          onBlur={emit}
          className="prose prose-sm min-h-[4.5rem] max-w-none px-3 py-2 text-sm text-ink focus:outline-none"
        />
        {showPlaceholder && placeholder && (
          <span className="pointer-events-none absolute left-3 top-2 select-none text-sm text-muted">
            {placeholder}
          </span>
        )}
      </div>
    </div>
  );
}

function ToolbarButton({
  label,
  title,
  onClick,
  children,
}: {
  label: string;
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={title}
      // Use onMouseDown + preventDefault so the editor's selection isn't lost
      // when the button takes focus (formatting must apply to the current range).
      onMouseDown={(e) => {
        e.preventDefault();
        onClick();
      }}
      className="flex h-7 w-7 items-center justify-center rounded text-sm text-ink hover:bg-panel"
    >
      {children}
    </button>
  );
}
