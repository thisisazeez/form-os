// ============================================================================
// QuestionEditor — rich-text DESCRIPTION wiring tests.
//
// Verifies the builder stores the description as `question.descriptionHtml`
// (sanitized), is multi-language aware, migrates a legacy plain `subheader`,
// and preserves recall tokens — without a heavyweight editor (jsdom has no
// execCommand, so we drive the contentEditable's emit path directly).
// ============================================================================
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Question, Survey } from "../../lib/types";
import { QuestionEditor } from "./QuestionEditor";

function q(overrides: Partial<Question> = {}): Question {
  return { id: "q1", type: "openText", headline: { default: "H", en: "H" }, config: {}, ...overrides };
}

function survey(questions: Question[]): Survey {
  return { name: "S", questions, languages: [{ code: "en", default: true }], hiddenFields: [] };
}

/** The description contentEditable surface. */
function descEditable(): HTMLElement {
  return screen.getByRole("textbox", { name: /question description/i });
}

describe("QuestionEditor — rich-text description", () => {
  it("renders a Description field with a formatting toolbar (not a plain input)", () => {
    render(<QuestionEditor question={q()} onChange={vi.fn()} lang="en" defaultLang="en" />);
    expect(screen.getByText("Description")).toBeInTheDocument();
    expect(screen.getByRole("toolbar", { name: /description formatting/i })).toBeInTheDocument();
  });

  it("stores edited HTML into descriptionHtml (sanitized) for the active language", () => {
    const onChange = vi.fn();
    render(<QuestionEditor question={q()} onChange={onChange} lang="en" defaultLang="en" />);
    const el = descEditable();
    el.innerHTML = "<b>Important</b> note<script>alert(1)</script>";
    fireEvent.input(el);
    expect(onChange).toHaveBeenCalled();
    const updated = onChange.mock.calls.at(-1)![0] as Question;
    // default lang "en" writes the `default` key.
    expect(updated.descriptionHtml?.default).toContain("<b>Important</b>");
    expect(updated.descriptionHtml?.default?.toLowerCase()).not.toContain("<script");
  });

  it("seeds the editor from descriptionHtml and shows the current HTML", () => {
    render(
      <QuestionEditor
        question={q({ descriptionHtml: { default: "<b>Seeded</b>", en: "<b>Seeded</b>" } })}
        onChange={vi.fn()}
        lang="en"
        defaultLang="en"
      />,
    );
    expect(descEditable().innerHTML).toContain("<b>Seeded</b>");
  });

  it("is multi-language aware: writing a non-default language preserves the default key", () => {
    const onChange = vi.fn();
    render(
      <QuestionEditor
        question={q({ descriptionHtml: { default: "<b>EN</b>", en: "<b>EN</b>" } })}
        onChange={onChange}
        lang="es"
        defaultLang="en"
      />,
    );
    const el = descEditable();
    el.innerHTML = "<b>ES</b>";
    fireEvent.input(el);
    const updated = onChange.mock.calls.at(-1)![0] as Question;
    expect(updated.descriptionHtml?.es).toContain("<b>ES</b>");
    expect(updated.descriptionHtml?.default).toBe("<b>EN</b>"); // default untouched
    expect(updated.descriptionHtml?.en).toBe("<b>EN</b>");
  });

  it("seeds (HTML-escaped) from a legacy plain subheader when no descriptionHtml exists", () => {
    render(
      <QuestionEditor
        question={q({ subheader: { default: "a < b & c", en: "a < b & c" } })}
        onChange={vi.fn()}
        lang="en"
        defaultLang="en"
      />,
    );
    // The legacy subheader appears as LITERAL text (escaped), not broken markup.
    expect(descEditable().innerHTML).toContain("a &lt; b &amp; c");
  });

  it("migrates a legacy subheader into descriptionHtml and clears that language's subheader on edit", () => {
    const onChange = vi.fn();
    render(
      <QuestionEditor
        question={q({ subheader: { default: "Old plain", en: "Old plain" } })}
        onChange={onChange}
        lang="en"
        defaultLang="en"
      />,
    );
    const el = descEditable();
    el.innerHTML = "<b>New rich</b>";
    fireEvent.input(el);
    const updated = onChange.mock.calls.at(-1)![0] as Question;
    expect(updated.descriptionHtml?.default).toContain("<b>New rich</b>");
    // Legacy subheader for the same language is dropped (no double-render at runtime).
    expect(updated.subheader?.default).toBeUndefined();
  });

  it("inserts a recall token into the description and preserves it (token survives sanitize)", async () => {
    const onChange = vi.fn();
    const s = survey([q({ id: "q1", headline: { default: "Your name?" } }), q({ id: "q2", headline: { default: "Greeting" } })]);
    render(<QuestionEditor question={s.questions[1]} onChange={onChange} lang="default" defaultLang="default" survey={s} />);

    // Two "@ Recall" buttons (headline + description); the SECOND is the description's.
    const recallButtons = screen.getAllByRole("button", { name: /@ Recall/ });
    expect(recallButtons.length).toBe(2);
    await userEvent.click(recallButtons[1]);
    const option = await screen.findByRole("option", { name: /Your name\?/ });
    await userEvent.click(option);

    const updated = onChange.mock.calls.at(-1)![0] as Question;
    expect(updated.descriptionHtml?.default).toContain("#recall:q1/fallback:#");
  });
});
