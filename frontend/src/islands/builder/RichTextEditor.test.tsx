// ============================================================================
// Tests for the rich-text description editor (RichTextEditor.tsx).
//
// jsdom does NOT implement document.execCommand, so toolbar-driven formatting
// can't be exercised end-to-end here (the component degrades gracefully). We
// instead verify the EMIT path that the security/storage contract depends on:
//   - editing the contentEditable emits SANITIZED HTML upward
//   - an empty editor emits "" (clears descriptionHtml)
//   - the toolbar + textbox render with accessible roles
//   - the imperative insertText API appends a recall token + emits
//   - external value changes re-sync the DOM
// Toolbar formatting (execCommand) is covered by real browsers; the sanitizer's
// tag-keeping is unit-tested separately in sanitizeHtml.test.ts.
// ============================================================================
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RichTextEditor, type RichTextEditorApi } from "./RichTextEditor";

/** Find the contentEditable surface (role=textbox, aria-multiline). */
function editable(): HTMLElement {
  return screen.getByRole("textbox", { name: /question description/i });
}

describe("RichTextEditor", () => {
  it("renders the formatting toolbar with bold/italic/underline/link buttons", () => {
    render(<RichTextEditor value="" onChange={vi.fn()} />);
    expect(screen.getByRole("toolbar", { name: /description formatting/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^bold$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^italic$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^underline$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /insert link/i })).toBeInTheDocument();
  });

  it("shows the placeholder when empty and hides it once there is content", () => {
    const { rerender } = render(
      <RichTextEditor value="" onChange={vi.fn()} placeholder="Add a description" />,
    );
    expect(screen.getByText("Add a description")).toBeInTheDocument();
    rerender(<RichTextEditor value="<b>hi</b>" onChange={vi.fn()} placeholder="Add a description" />);
    expect(screen.queryByText("Add a description")).not.toBeInTheDocument();
  });

  it("emits SANITIZED HTML on input (script payloads stripped, safe tags kept)", () => {
    const onChange = vi.fn();
    render(<RichTextEditor value="" onChange={onChange} />);
    const el = editable();
    // Simulate the author having typed bold text + a malicious script.
    el.innerHTML = "<b>bold</b><script>alert(1)</script>";
    fireEvent.input(el);
    expect(onChange).toHaveBeenCalled();
    const emitted = onChange.mock.calls.at(-1)![0] as string;
    expect(emitted).toContain("<b>bold</b>");
    expect(emitted.toLowerCase()).not.toContain("<script");
    expect(emitted.toLowerCase()).not.toContain("alert");
  });

  it("emits '' when the editor is cleared (only <br>/&nbsp; noise)", () => {
    const onChange = vi.fn();
    render(<RichTextEditor value="<p>x</p>" onChange={onChange} />);
    const el = editable();
    el.innerHTML = "<br>";
    fireEvent.input(el);
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("preserves a recall token typed into the description (token survives sanitize)", () => {
    const onChange = vi.fn();
    render(<RichTextEditor value="" onChange={onChange} />);
    const el = editable();
    el.innerHTML = "Hi <b>#recall:name/fallback:there#</b>";
    fireEvent.input(el);
    const emitted = onChange.mock.calls.at(-1)![0] as string;
    expect(emitted).toContain("#recall:name/fallback:there#");
    expect(emitted).toContain("<b>");
  });

  it("insertText appends a recall token and emits sanitized HTML", () => {
    const onChange = vi.fn();
    let api: RichTextEditorApi | null = null;
    render(
      <RichTextEditor
        value="Hello"
        onChange={onChange}
        onReady={(a) => {
          api = a;
        }}
      />,
    );
    // jsdom has no execCommand → insertText falls back to appending a text node.
    expect(api).not.toBeNull();
    api!.insertText("#recall:q1/fallback:#");
    const emitted = onChange.mock.calls.at(-1)![0] as string;
    expect(emitted).toContain("#recall:q1/fallback:#");
    expect(emitted).toContain("Hello");
  });

  it("re-syncs the DOM when the value changes externally (language switch)", () => {
    const { rerender } = render(<RichTextEditor value="<b>english</b>" onChange={vi.fn()} />);
    const el = editable();
    expect(el.innerHTML).toContain("english");
    rerender(<RichTextEditor value="<b>français</b>" onChange={vi.fn()} />);
    expect(el.innerHTML).toContain("français");
    expect(el.innerHTML).not.toContain("english");
  });
});
