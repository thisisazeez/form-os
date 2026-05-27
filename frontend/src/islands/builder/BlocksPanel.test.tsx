// ============================================================================
// BlocksPanel tests — the question-grouping authoring editor (Blocks tab).
//
// Covers the authoring surface: create a block, set a title, group/ungroup member
// questions, set per-block randomize, reorder blocks, and ungroup a block. The panel
// is CONTROLLED — it emits a fresh Survey via onChange (surveyModel transforms), so the
// harness re-renders with the new survey to assert the next edit. CRITICAL: grouping
// never reorders survey.questions (the flat array is asserted intact).
// ============================================================================
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type { Question, Survey } from "../../lib/types";
import { BlocksPanel } from "./BlocksPanel";

function q(id: string, headline = id): Question {
  return { id, type: "openText", headline: { default: headline }, config: {} };
}

function baseSurvey(): Survey {
  return {
    name: "S",
    questions: [q("q1", "First"), q("q2", "Second"), q("q3", "Third")],
    languages: [{ code: "en", default: true }],
  };
}

/** A controlled harness exposing the latest survey for assertions. */
function Harness({ initial, onSurvey }: { initial: Survey; onSurvey?: (s: Survey) => void }) {
  const [survey, setSurvey] = useState(initial);
  return (
    <BlocksPanel
      survey={survey}
      onChange={(s) => {
        setSurvey(s);
        onSurvey?.(s);
      }}
    />
  );
}

describe("BlocksPanel", () => {
  it("starts empty and creates a block", async () => {
    const user = userEvent.setup();
    let latest: Survey | null = null;
    render(<Harness initial={baseSurvey()} onSurvey={(s) => (latest = s)} />);
    expect(screen.getByText(/No blocks yet/i)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /create block/i }));
    expect(latest!.blocks).toHaveLength(1);
    expect(screen.getByTestId("block-card")).toBeTruthy();
  });

  it("adds a question to a block without reordering questions[]", async () => {
    const user = userEvent.setup();
    let latest: Survey | null = null;
    render(<Harness initial={baseSurvey()} onSurvey={(s) => (latest = s)} />);
    await user.click(screen.getByRole("button", { name: /create block/i }));

    const addSelect = screen.getByRole("combobox", { name: /add a question to block 1/i });
    await user.selectOptions(addSelect, "q2");

    expect(latest!.blocks![0].questionIds).toEqual(["q2"]);
    // The flat questions[] order is untouched.
    expect(latest!.questions.map((x) => x.id)).toEqual(["q1", "q2", "q3"]);
  });

  it("sets a block title and randomize mode", async () => {
    const user = userEvent.setup();
    let latest: Survey | null = null;
    render(<Harness initial={baseSurvey()} onSurvey={(s) => (latest = s)} />);
    await user.click(screen.getByRole("button", { name: /create block/i }));

    const card = screen.getByTestId("block-card");
    const titleInput = within(card).getByLabelText(/block 1 title/i);
    await user.clear(titleInput);
    await user.type(titleInput, "Demographics");
    expect(latest!.blocks![0].title).toBe("Demographics");

    const randomize = within(card).getByLabelText(/block 1 randomize/i);
    await user.selectOptions(randomize, "all");
    expect(latest!.blocks![0].randomize).toBe("all");
  });

  it("removes a question from a block (question stays in survey)", async () => {
    const user = userEvent.setup();
    let latest: Survey | null = null;
    render(<Harness initial={baseSurvey()} onSurvey={(s) => (latest = s)} />);
    await user.click(screen.getByRole("button", { name: /create block/i }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: /add a question to block 1/i }),
      "q1",
    );
    await user.click(screen.getByRole("button", { name: /remove q1 from block 1/i }));
    expect(latest!.blocks![0].questionIds).toEqual([]);
    expect(latest!.questions.map((x) => x.id)).toEqual(["q1", "q2", "q3"]);
  });

  it("reorders blocks (metadata only) and ungroups", async () => {
    const user = userEvent.setup();
    let latest: Survey | null = null;
    render(<Harness initial={baseSurvey()} onSurvey={(s) => (latest = s)} />);
    await user.click(screen.getByRole("button", { name: /create block/i }));
    await user.click(screen.getByRole("button", { name: /create block/i }));
    expect(latest!.blocks).toHaveLength(2);

    // Move block 1 down -> the two blocks swap; questions[] never changes.
    await user.click(screen.getByRole("button", { name: /move block 1 down/i }));
    expect(latest!.questions.map((x) => x.id)).toEqual(["q1", "q2", "q3"]);

    // Ungroup block 1 -> one block remains.
    await user.click(screen.getAllByRole("button", { name: /ungroup block 1/i })[0]);
    expect(latest!.blocks).toHaveLength(1);
  });

  it("shows ungrouped questions footer", () => {
    render(<Harness initial={baseSurvey()} />);
    const footer = screen.getByText(/Ungrouped questions/i).closest("div")!;
    // All three questions are ungrouped initially.
    expect(within(footer).getByText("First")).toBeTruthy();
    expect(within(footer).getByText("Second")).toBeTruthy();
    expect(within(footer).getByText("Third")).toBeTruthy();
  });
});
