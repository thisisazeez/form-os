// ============================================================================
// Builder recall/piping + hidden-fields UI tests.
//
//   - recallSourcesFor(): only EARLIER questions + all hidden fields (never the
//     current/later questions — a question can't recall a later/unanswered one).
//   - QuestionEditor "@ Recall" control: inserts the FROZEN token into the active
//     headline field; absent when there are no sources.
//   - HiddenFieldsPanel: add/remove a hidden field by key.
// ============================================================================
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Question, Survey } from "../../lib/types";
import { HiddenFieldsPanel } from "./HiddenFieldsPanel";
import { QuestionEditor, recallSourcesFor, recallToken } from "./QuestionEditor";

function q(id: string, headline: string): Question {
  return { id, type: "openText", headline: { default: headline }, config: {} };
}

function survey(questions: Question[], hiddenFields: Survey["hiddenFields"] = []): Survey {
  return { name: "S", questions, languages: [{ code: "en", default: true }], hiddenFields };
}

describe("recallSourcesFor", () => {
  it("lists only EARLIER questions for the host (never itself or later ones)", () => {
    const s = survey([q("q1", "First"), q("q2", "Second"), q("q3", "Third")]);
    const sources = recallSourcesFor(s, s.questions[1], "default"); // host = q2
    expect(sources.map((x) => x.id)).toEqual(["q1"]); // only the earlier q1
    expect(sources[0]).toMatchObject({ id: "q1", kind: "question", label: "First" });
  });

  it("includes all hidden fields regardless of question position", () => {
    const s = survey([q("q1", "First")], [{ id: "userId", name: "User ID" }, { id: "plan" }]);
    const sources = recallSourcesFor(s, s.questions[0], "default"); // host = q1 (first → no earlier q)
    // No earlier questions, but both hidden fields are offered.
    expect(sources.map((x) => x.id)).toEqual(["userId", "plan"]);
    expect(sources[0]).toMatchObject({ id: "userId", kind: "hidden", label: "User ID" });
    expect(sources[1]).toMatchObject({ id: "plan", kind: "hidden", label: "plan" }); // falls back to id
  });

  it("returns [] for a missing survey", () => {
    expect(recallSourcesFor(undefined, q("q1", "x"), "default")).toEqual([]);
  });
});

describe("recallToken", () => {
  it("builds the FROZEN token format", () => {
    expect(recallToken("userId")).toBe("#recall:userId/fallback:#");
    expect(recallToken("q1", "there")).toBe("#recall:q1/fallback:there#");
  });
});

describe("QuestionEditor — @ Recall insert control", () => {
  it("is absent for the first question with no hidden fields (single-question flow unchanged)", () => {
    const s = survey([q("q1", "Only question")]);
    render(<QuestionEditor question={s.questions[0]} onChange={vi.fn()} survey={s} />);
    expect(screen.queryByRole("button", { name: /@ Recall/ })).not.toBeInTheDocument();
  });

  it("inserts a recall token into the headline (active language) when a source is chosen", async () => {
    const s = survey([q("q1", "Your name?"), q("q2", "Greeting")]);
    const onChange = vi.fn();
    // Host = q2; q1 is the only earlier source.
    render(<QuestionEditor question={s.questions[1]} onChange={onChange} survey={s} />);

    // Two "@ Recall" buttons (headline + subheader); open the first (headline).
    const recallButtons = screen.getAllByRole("button", { name: /@ Recall/ });
    expect(recallButtons.length).toBeGreaterThanOrEqual(1);
    await userEvent.click(recallButtons[0]);

    // Choose the q1 option.
    const option = await screen.findByRole("option", { name: /Your name\?/ });
    await userEvent.click(option);

    // onChange fired with the token appended to the headline's default key.
    expect(onChange).toHaveBeenCalled();
    const updated = onChange.mock.calls[onChange.mock.calls.length - 1][0] as Question;
    expect(updated.headline.default).toBe("Greeting #recall:q1/fallback:#");
  });

  it("offers hidden fields in the insert list", async () => {
    const s = survey([q("q1", "Greeting")], [{ id: "userId", name: "User ID" }]);
    const onChange = vi.fn();
    render(<QuestionEditor question={s.questions[0]} onChange={onChange} survey={s} />);

    const recallButtons = screen.getAllByRole("button", { name: /@ Recall/ });
    await userEvent.click(recallButtons[0]);
    const option = await screen.findByRole("option", { name: /User ID/ });
    await userEvent.click(option);

    const updated = onChange.mock.calls[onChange.mock.calls.length - 1][0] as Question;
    expect(updated.headline.default).toBe("Greeting #recall:userId/fallback:#");
  });
});

describe("HiddenFieldsPanel", () => {
  it("adds a hidden field by key (and optional label)", async () => {
    const s = survey([q("q1", "Q1")]);
    const onChange = vi.fn();
    render(<HiddenFieldsPanel survey={s} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("New hidden field key"), "userId");
    await userEvent.type(screen.getByLabelText("New hidden field label"), "User ID");
    await userEvent.click(screen.getByRole("button", { name: /Add hidden field/ }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as Survey;
    expect(next.hiddenFields).toEqual([{ id: "userId", name: "User ID" }]);
  });

  it("rejects an id that collides with a question id", async () => {
    const s = survey([q("q1", "Q1")]);
    const onChange = vi.fn();
    render(<HiddenFieldsPanel survey={s} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("New hidden field key"), "q1");
    await userEvent.click(screen.getByRole("button", { name: /Add hidden field/ }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/collides/i);
  });

  it("rejects an invalid key (charset)", async () => {
    const onChange = vi.fn();
    render(<HiddenFieldsPanel survey={survey([])} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("New hidden field key"), "1bad");
    await userEvent.click(screen.getByRole("button", { name: /Add hidden field/ }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("renders + removes an existing hidden field", async () => {
    const s = survey([q("q1", "Q1")], [{ id: "userId", name: "User ID" }]);
    const onChange = vi.fn();
    render(<HiddenFieldsPanel survey={s} onChange={onChange} />);

    const panel = screen.getByTestId("hidden-fields-panel");
    expect(within(panel).getByText("userId")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Remove hidden field userId/ }));
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as Survey;
    expect(next.hiddenFields).toEqual([]);
  });
});
