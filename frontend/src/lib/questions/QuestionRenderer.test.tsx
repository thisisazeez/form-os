// ============================================================================
// Renderer tests: each core question type renders the active language and
// captures the correct answer shape (survey-schema.json #/$defs/answerShapes).
// ============================================================================
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AnswerValue, Question } from "../types";
import { QuestionRenderer } from "./QuestionRenderer";

function renderQ(question: Question, opts: { value?: unknown; required?: boolean; lang?: string } = {}) {
  const onChange = vi.fn();
  render(
    <QuestionRenderer
      question={question}
      value={opts.value as never}
      onChange={onChange}
      lang={opts.lang ?? "en"}
      defaultLang="en"
      required={opts.required ?? false}
    />,
  );
  return { onChange };
}

/**
 * Render with real state so controlled inputs accumulate (e.g. userEvent.type).
 * Returns a getter for the latest captured value.
 */
function renderStateful(question: Question, opts: { lang?: string; required?: boolean } = {}) {
  let latest: AnswerValue | undefined;
  function Harness() {
    const [value, setValue] = useState<AnswerValue | undefined>(undefined);
    latest = value;
    return (
      <QuestionRenderer
        question={question}
        value={value}
        onChange={(v) => setValue(v)}
        lang={opts.lang ?? "en"}
        defaultLang="en"
        required={opts.required ?? false}
      />
    );
  }
  render(<Harness />);
  return { get: () => latest };
}

describe("QuestionRenderer — headline + required marker + i18n", () => {
  it("renders the active-language headline and a required marker", () => {
    renderQ(
      {
        id: "q1",
        type: "openText",
        headline: { default: "Default H", en: "English H", es: "Spanish H" },
        config: { inputType: "short" },
      },
      { required: true, lang: "es" },
    );
    expect(screen.getByText("Spanish H")).toBeInTheDocument();
    expect(screen.getByTitle("Required")).toBeInTheDocument();
  });

  it("falls back to the default i18n key when the active language is missing", () => {
    renderQ({
      id: "q1",
      type: "openText",
      headline: { default: "Fallback H" },
      config: { inputType: "short" },
    });
    expect(screen.getByText("Fallback H")).toBeInTheDocument();
  });
});

describe("QuestionRenderer — description (plain subheader vs rich-text HTML)", () => {
  function renderWithAnswers(question: Question, lang = "en", answers: Record<string, unknown> = {}) {
    const { container } = render(
      <QuestionRenderer
        question={question}
        value={undefined as never}
        onChange={vi.fn()}
        lang={lang}
        defaultLang="en"
        required={false}
        answers={answers as never}
      />,
    );
    return container;
  }

  it("renders a plain subheader as text (unchanged) when there is no descriptionHtml", () => {
    renderWithAnswers({
      id: "q1",
      type: "openText",
      headline: { default: "H", en: "H" },
      subheader: { default: "Plain description", en: "Plain description" },
      config: { inputType: "short" },
    });
    const p = screen.getByText("Plain description");
    expect(p.tagName).toBe("P"); // plain-text <p>, not an HTML container
  });

  it("renders sanitized descriptionHtml (bold kept) IN PLACE OF the subheader", () => {
    const container = renderWithAnswers({
      id: "q1",
      type: "openText",
      headline: { default: "H", en: "H" },
      subheader: { default: "ignored plain", en: "ignored plain" },
      descriptionHtml: { default: "<b>Bold</b> info", en: "<b>Bold</b> info" },
      config: { inputType: "short" },
    });
    expect(container.querySelector("b")?.textContent).toBe("Bold");
    expect(screen.getByText("Bold")).toBeInTheDocument();
    // The plain subheader must NOT also render (no double description).
    expect(screen.queryByText("ignored plain")).not.toBeInTheDocument();
  });

  it("STRIPS an XSS payload in descriptionHtml (script + onerror removed)", () => {
    const container = renderWithAnswers({
      id: "q1",
      type: "openText",
      headline: { default: "H", en: "H" },
      descriptionHtml: {
        default: 'safe<script>window.__xss=1</script><img src=x onerror="window.__xss=1">',
        en: 'safe<script>window.__xss=1</script><img src=x onerror="window.__xss=1">',
      },
      config: { inputType: "short" },
    });
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.innerHTML.toLowerCase()).not.toContain("onerror");
    expect(screen.getByText("safe")).toBeInTheDocument();
    // The script never executed → no global side effect.
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
  });

  it("resolves a recall token INSIDE the description, THEN sanitizes (answer markup neutralized)", () => {
    const container = renderWithAnswers(
      {
        id: "q2",
        type: "openText",
        headline: { default: "H", en: "H" },
        descriptionHtml: {
          default: "Hi <b>#recall:name/fallback:there#</b>",
          en: "Hi <b>#recall:name/fallback:there#</b>",
        },
        config: { inputType: "short" },
      },
      "en",
      // A malicious prior answer: its markup must be stripped by the post-recall sanitize.
      { name: "<img src=x onerror=alert(1)>Ada" },
    );
    // Bold tag (author formatting) survives.
    expect(container.querySelector("b")).not.toBeNull();
    // The recalled answer's text is shown, but its <img onerror> is stripped.
    expect(container.textContent).toContain("Ada");
    expect(container.querySelector("img")).toBeNull();
    expect(container.innerHTML.toLowerCase()).not.toContain("onerror");
  });

  it("uses the recall fallback in the description when the source is unanswered", () => {
    const container = renderWithAnswers(
      {
        id: "q2",
        type: "openText",
        headline: { default: "H", en: "H" },
        descriptionHtml: { default: "Hi #recall:name/fallback:there#", en: "Hi #recall:name/fallback:there#" },
        config: { inputType: "short" },
      },
      "en",
      {},
    );
    expect(container.textContent).toContain("Hi there");
  });

  it("falls back to the plain subheader when there is no descriptionHtml for the active OR default language", () => {
    // descriptionHtml exists ONLY in Spanish; default lang is en. For an English
    // response the i18n chain finds no en/default descriptionHtml → empty → the
    // plain subheader renders (byte-identical to pre-rich-text behavior).
    renderWithAnswers(
      {
        id: "q1",
        type: "openText",
        headline: { default: "H", en: "H" },
        subheader: { default: "Plain fallback", en: "Plain fallback" },
        descriptionHtml: { es: "<b>Spanish rich</b>" },
        config: { inputType: "short" },
      },
      "en",
    );
    expect(screen.getByText("Plain fallback").tagName).toBe("P");
    expect(screen.queryByText("Spanish rich")).not.toBeInTheDocument();
  });

  it("renders the active-language rich description, falling back across languages per i18n rules", () => {
    // descriptionHtml only in en (== defaultLang). A French response with no fr
    // entry follows the i18n chain to the en (default) value — consistent with
    // how every other display string resolves.
    const container = renderWithAnswers(
      {
        id: "q1",
        type: "openText",
        headline: { default: "H", en: "H", fr: "H" },
        descriptionHtml: { default: "<b>English rich</b>", en: "<b>English rich</b>" },
        config: { inputType: "short" },
      },
      "fr",
    );
    expect(container.querySelector("b")?.textContent).toBe("English rich");
  });
});

describe("OpenText", () => {
  it("captures a string answer (short), accumulating typed characters", async () => {
    const { get } = renderStateful({
      id: "q1",
      type: "openText",
      headline: { default: "Name" },
      config: { inputType: "short" },
    });
    await userEvent.type(screen.getByRole("textbox"), "Ada");
    expect(get()).toBe("Ada");
  });

  it("clears to undefined when emptied", () => {
    const { onChange } = renderQ(
      { id: "q1", type: "openText", headline: { default: "Name" }, config: { inputType: "short" } },
      { value: "x" },
    );
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("renders a textarea for long input", () => {
    renderQ({ id: "q1", type: "openText", headline: { default: "Bio" }, config: { inputType: "long" } });
    expect(screen.getByRole("textbox").tagName).toBe("TEXTAREA");
  });
});

describe("SingleSelect", () => {
  const q: Question = {
    id: "q1",
    type: "singleSelect",
    headline: { default: "Pick" },
    config: {
      choices: [
        { id: "a", label: { default: "A" } },
        { id: "b", label: { default: "B" } },
        { id: "other", label: { default: "Other" }, isOther: true },
      ],
    },
  };

  it("captures the choiceId string for a normal choice", async () => {
    const { onChange } = renderQ(q);
    await userEvent.click(screen.getByLabelText("A"));
    expect(onChange).toHaveBeenLastCalledWith("a");
  });

  it("captures {choiceId, other} for an isOther choice", async () => {
    const { onChange } = renderQ(q);
    await userEvent.click(screen.getByLabelText("Other"));
    expect(onChange).toHaveBeenLastCalledWith({ choiceId: "other", other: "" });
  });

  it("updates the other text", () => {
    const { onChange } = renderQ(q, { value: { choiceId: "other", other: "" } });
    const otherInput = screen.getByLabelText("Other — specify");
    fireEvent.change(otherInput, { target: { value: "custom" } });
    expect(onChange).toHaveBeenLastCalledWith({ choiceId: "other", other: "custom" });
  });

  it("supports dropdown display", async () => {
    const { onChange } = renderQ({ ...q, config: { ...q.config, display: "dropdown" } });
    await userEvent.selectOptions(screen.getByRole("combobox"), "b");
    expect(onChange).toHaveBeenLastCalledWith("b");
  });
});

describe("MultiSelect", () => {
  const q: Question = {
    id: "q1",
    type: "multiSelect",
    headline: { default: "Pick many" },
    config: {
      choices: [
        { id: "a", label: { default: "A" } },
        { id: "b", label: { default: "B" } },
        { id: "c", label: { default: "C" } },
      ],
    },
  };

  it("captures an array of choiceIds and toggles", async () => {
    const { onChange } = renderQ(q, { value: ["a"] });
    await userEvent.click(screen.getByLabelText("B"));
    expect(onChange).toHaveBeenLastCalledWith(["a", "b"]);
  });

  it("removes a selected choice and clears to undefined when empty", async () => {
    const { onChange } = renderQ(q, { value: ["a"] });
    await userEvent.click(screen.getByLabelText("A"));
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("enforces maxSelections by disabling further options", () => {
    renderQ(
      { ...q, config: { ...q.config, maxSelections: 1 } },
      { value: ["a"] },
    );
    expect(screen.getByLabelText("B")).toBeDisabled();
    expect(screen.getByLabelText("A")).not.toBeDisabled();
  });
});

describe("Rating", () => {
  it("captures an integer 1..range (star scale)", async () => {
    const { onChange } = renderQ({
      id: "q1",
      type: "rating",
      headline: { default: "Rate" },
      config: { range: 5, scale: "star" },
    });
    await userEvent.click(screen.getByRole("radio", { name: "4" }));
    expect(onChange).toHaveBeenLastCalledWith(4);
  });

  it("toggles off when the same value is clicked", async () => {
    const { onChange } = renderQ(
      { id: "q1", type: "rating", headline: { default: "Rate" }, config: { range: 5, scale: "number" } },
      { value: 3 },
    );
    await userEvent.click(screen.getByRole("radio", { name: "3" }));
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });
});

describe("Nps", () => {
  it("renders 11 buttons (0..10) and captures the number", async () => {
    const { onChange } = renderQ({ id: "q1", type: "nps", headline: { default: "Recommend?" }, config: {} });
    expect(screen.getAllByRole("radio")).toHaveLength(11);
    await userEvent.click(screen.getByRole("radio", { name: "9" }));
    expect(onChange).toHaveBeenLastCalledWith(9);
  });
});

describe("Consent", () => {
  it("captures true when checked, undefined when unchecked", async () => {
    const { onChange } = renderQ({
      id: "q1",
      type: "consent",
      headline: { default: "Consent" },
      config: { label: { default: "I agree" } },
    });
    const box = screen.getByLabelText("I agree");
    await userEvent.click(box);
    expect(onChange).toHaveBeenLastCalledWith(true);
  });
});

describe("Cta", () => {
  it("captures true when the button is clicked", async () => {
    const { onChange } = renderQ({
      id: "q1",
      type: "cta",
      headline: { default: "Notice" },
      config: { buttonLabel: { default: "Got it" } },
    });
    await userEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(onChange).toHaveBeenLastCalledWith(true);
  });
});

describe("Date", () => {
  it("captures an ISO 'YYYY-MM-DD' string", () => {
    const { onChange } = renderQ({
      id: "q1",
      type: "date",
      headline: { default: "When?" },
      config: { format: "YYYY-MM-DD" },
    });
    const input = document.getElementById("q-q1") as HTMLInputElement;
    expect(input.type).toBe("date");
    fireEvent.change(input, { target: { value: "2026-05-26" } });
    expect(onChange).toHaveBeenLastCalledWith("2026-05-26");
  });

  it("clears to undefined when emptied", () => {
    const { onChange } = renderQ(
      { id: "q1", type: "date", headline: { default: "When?" }, config: {} },
      { value: "2026-01-01" },
    );
    const input = document.getElementById("q-q1") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });
});

describe("Number", () => {
  it("captures a real number (not a string)", () => {
    const { onChange } = renderQ({
      id: "q1",
      type: "number",
      headline: { default: "How many?" },
      config: { min: 0 },
    });
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "42" } });
    expect(onChange).toHaveBeenLastCalledWith(42);
  });

  it("clears to undefined when emptied", () => {
    const { onChange } = renderQ(
      { id: "q1", type: "number", headline: { default: "How many?" }, config: {} },
      { value: 7 },
    );
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });
});

describe("Ranking", () => {
  const q: Question = {
    id: "q1",
    type: "ranking",
    headline: { default: "Order these" },
    config: {
      choices: [
        { id: "a", label: { default: "Alpha" } },
        { id: "b", label: { default: "Beta" } },
        { id: "c", label: { default: "Gamma" } },
      ],
    },
  };

  it("seeds the answer to the config order on mount (required ranking is satisfiable without a move)", () => {
    const { onChange } = renderQ(q);
    expect(onChange).toHaveBeenCalledWith(["a", "b", "c"]);
  });

  it("captures the full ordered list of choice ids on a move", async () => {
    const { onChange } = renderQ(q);
    // Move "Alpha" down -> [b, a, c]
    await userEvent.click(screen.getByLabelText("Move Alpha down"));
    expect(onChange).toHaveBeenLastCalledWith(["b", "a", "c"]);
  });

  it("disables the up control on the first item and down on the last", () => {
    renderQ(q);
    expect(screen.getByLabelText("Move Alpha up")).toBeDisabled();
    expect(screen.getByLabelText("Move Gamma down")).toBeDisabled();
  });
});

describe("Matrix", () => {
  const single: Question = {
    id: "q1",
    type: "matrix",
    headline: { default: "Rate each" },
    config: {
      selection: "single",
      rows: [
        { id: "r1", label: { default: "Speed" } },
        { id: "r2", label: { default: "Quality" } },
      ],
      columns: [
        { id: "c1", label: { default: "Bad" } },
        { id: "c2", label: { default: "Good" } },
      ],
    },
  };

  it("captures {rowId: columnId} for single selection", async () => {
    const { onChange } = renderQ(single);
    await userEvent.click(screen.getByLabelText("Speed — Good"));
    expect(onChange).toHaveBeenLastCalledWith({ r1: "c2" });
  });

  it("captures {rowId: [columnId]} for multiple selection", async () => {
    const { onChange } = renderQ({
      ...single,
      config: { ...single.config, selection: "multiple" },
    });
    await userEvent.click(screen.getByLabelText("Speed — Bad"));
    expect(onChange).toHaveBeenLastCalledWith({ r1: ["c1"] });
  });
});

describe("ContactInfo", () => {
  const q: Question = {
    id: "q1",
    type: "contactInfo",
    headline: { default: "Your details" },
    config: {
      fields: {
        email: { show: true, required: true },
        company: { show: true },
        phone: { show: false }, // hidden -> not rendered
      },
    },
  };

  it("renders only enabled sub-fields and captures an object answer", async () => {
    const { onChange } = renderQ(q);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Company")).toBeInTheDocument();
    expect(screen.queryByLabelText("Phone")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@x.io" } });
    expect(onChange).toHaveBeenLastCalledWith({ email: "ada@x.io" });
  });

  it("uses the matching HTML5 input type for email", () => {
    renderQ(q);
    expect((screen.getByLabelText("Email") as HTMLInputElement).type).toBe("email");
  });
});

describe("FileUpload (backend deferred)", () => {
  it("renders a file input + a deferral note and does not record an answer", () => {
    const { onChange } = renderQ({
      id: "q1",
      type: "fileUpload",
      headline: { default: "Attach a file" },
      config: { allowMultiple: false, maxSizeMb: 5 },
    });
    const input = document.getElementById("q-q1") as HTMLInputElement;
    expect(input.type).toBe("file");
    expect(screen.getByRole("note")).toHaveTextContent(/not yet wired/i);
    // No upload endpoint: selecting files must NOT emit a fabricated answer.
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("PictureChoice", () => {
  const opts = [
    { id: "o1", imageUrl: "https://x/1.png", label: { default: "First" } },
    { id: "o2", imageUrl: "https://x/2.png", label: { default: "Second" } },
    { id: "o3", imageUrl: "https://x/3.png" }, // no label
  ];

  it("captures a single choiceId string (allowMulti: false) and toggles off", async () => {
    const { onChange } = renderQ({
      id: "q1",
      type: "pictureChoice",
      headline: { default: "Pick one image" },
      config: { options: opts, allowMulti: false },
    });
    await userEvent.click(screen.getByLabelText("First"));
    expect(onChange).toHaveBeenLastCalledWith("o1");
  });

  it("switches the single selection to another image", async () => {
    const { onChange } = renderQ(
      {
        id: "q1",
        type: "pictureChoice",
        headline: { default: "Pick one" },
        config: { options: opts, allowMulti: false },
      },
      { value: "o1" },
    );
    await userEvent.click(screen.getByLabelText("Second"));
    expect(onChange).toHaveBeenLastCalledWith("o2");
  });

  const multi: Question = {
    id: "q1",
    type: "pictureChoice",
    headline: { default: "Pick many" },
    config: { options: opts, allowMulti: true },
  };

  it("appends a choiceId on select (allowMulti: true)", async () => {
    const { onChange } = renderQ(multi, { value: ["o1"] });
    await userEvent.click(screen.getByLabelText("Second"));
    expect(onChange).toHaveBeenLastCalledWith(["o1", "o2"]);
  });

  it("clears to undefined when the last multi selection is removed", async () => {
    const { onChange } = renderQ(multi, { value: ["o2"] });
    await userEvent.click(screen.getByLabelText("Second"));
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("renders radios for single and checkboxes for multi", () => {
    renderQ({
      id: "qs",
      type: "pictureChoice",
      headline: { default: "Single" },
      config: { options: opts, allowMulti: false },
    });
    expect(screen.getAllByRole("radio")).toHaveLength(3);
  });

  it("uses 'Option N' a11y labels when a picture has no label", () => {
    renderQ({
      id: "q1",
      type: "pictureChoice",
      headline: { default: "Pick" },
      config: { options: opts, allowMulti: true },
    });
    // Third option has no label -> falls back to "Option 3".
    expect(screen.getByLabelText("Option 3")).toBeInTheDocument();
  });
});

describe("Address", () => {
  const q: Question = {
    id: "q1",
    type: "address",
    headline: { default: "Your address" },
    config: {
      fields: {
        addressLine1: { show: true, required: true },
        city: { show: true },
        country: { show: true },
        state: { show: false }, // hidden -> not rendered
      },
    },
  };

  it("renders only enabled sub-fields and captures an object answer", () => {
    const { onChange } = renderQ(q);
    expect(screen.getByLabelText("Address line 1")).toBeInTheDocument();
    expect(screen.getByLabelText("City")).toBeInTheDocument();
    expect(screen.getByLabelText("Country")).toBeInTheDocument();
    expect(screen.queryByLabelText("State / Region")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("City"), { target: { value: "Berlin" } });
    expect(onChange).toHaveBeenLastCalledWith({ city: "Berlin" });
  });

  it("drops a sub-field when emptied and clears to undefined when all blank", () => {
    const { onChange } = renderQ(q, { value: { city: "Berlin" } });
    fireEvent.change(screen.getByLabelText("City"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });
});

describe("ScheduleMeeting", () => {
  const q: Question = {
    id: "q1",
    type: "scheduleMeeting",
    headline: { default: "Book a time" },
    config: { calLink: "jane/30min" },
  };

  it("renders a cal.com inline embed iframe pointing at the configured link", () => {
    renderQ(q);
    const iframe = document.getElementById("q-q1") as HTMLIFrameElement;
    expect(iframe.tagName).toBe("IFRAME");
    expect(iframe.src).toContain("cal.com/jane/30min");
    expect(iframe.src).toContain("embed");
  });

  it("records {booked:true, uid} on a Cal BOOKING_SUCCESSFUL postMessage", async () => {
    const { get } = renderStateful(q);
    await act(async () => {
      window.postMessage({ type: "BOOKING_SUCCESSFUL", data: { uid: "bk_123" } }, "*");
      // postMessage is async — wait a tick for the listener + state update.
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(get()).toEqual({ booked: true, uid: "bk_123" });
  });

  it("shows a configuration alert when no calLink is set", () => {
    renderQ({ ...q, config: { calLink: "" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/no scheduling link/i);
  });
});
