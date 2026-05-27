// ============================================================================
// QuotasPanel component tests (vitest + testing-library).
//
// Drives the Settings-tab Quotas editor: add/remove quotas, edit name + limit, edit the
// condition row (question/operator/value), and pick an over-quota ending. Asserts on the
// controlled survey state the panel emits.
// ============================================================================
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type { Survey } from "../../lib/types";
import { QuotasPanel } from "./QuotasPanel";

function baseSurvey(): Survey {
  return {
    name: "S",
    questions: [
      {
        id: "country",
        type: "singleSelect",
        headline: { default: "Country?" },
        config: { choices: [{ id: "us", label: { default: "US" } }] },
      },
    ],
    endings: [
      { id: "end1", type: "endScreen", headline: { default: "Thanks" } },
      { id: "quota_full", type: "endScreen", headline: { default: "We are full" } },
    ],
    languages: [{ code: "en", default: true }],
  };
}

function Harness({ initial }: { initial: Survey }) {
  const [survey, setSurvey] = useState(initial);
  const defaultLang = survey.languages.find((l) => l.default)?.code ?? "default";
  return (
    <>
      <QuotasPanel survey={survey} onChange={setSurvey} lang="default" defaultLang={defaultLang} />
      <pre data-testid="state">{JSON.stringify(survey.quotas ?? [])}</pre>
    </>
  );
}

function quotasState() {
  return JSON.parse(screen.getByTestId("state").textContent || "[]");
}

describe("QuotasPanel", () => {
  it("starts empty with an empty-state hint", () => {
    render(<Harness initial={baseSurvey()} />);
    expect(screen.getByText(/No quotas yet/)).toBeInTheDocument();
    expect(quotasState()).toEqual([]);
  });

  it("adds a quota with schema-shaped defaults", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add quota/ }));
    const state = quotasState();
    expect(state).toHaveLength(1);
    expect(state[0].limit).toBe(100);
    expect(state[0].condition.leftQuestionId).toBe("country");
    expect(state[0].endingId).toBeUndefined();
  });

  it("edits the quota name and limit", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add quota/ }));

    const name = screen.getByLabelText("Quota name");
    await user.clear(name);
    await user.type(name, "US cap");
    expect(quotasState()[0].name).toBe("US cap");

    // Appending a digit updates the limit (the input is controlled + clamps to an integer >= 1).
    // Default is 100; typing "5" appends -> 1005. We assert the panel forwards the edit to the
    // model as a valid integer >= 1 (the exact coercion is unit-tested in quotas.surveyModel).
    const limit = screen.getByLabelText("Quota limit");
    await user.type(limit, "5");
    const lim = quotasState()[0].limit;
    expect(Number.isInteger(lim)).toBe(true);
    expect(lim).toBeGreaterThanOrEqual(1);
    expect(lim).toBe(1005);
  });

  it("edits the condition row (operator + value)", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add quota/ }));

    const value = screen.getByLabelText("Condition value");
    await user.type(value, "us");
    expect(quotasState()[0].condition.value).toBe("us");

    // Switch to a presence operator -> the value field disappears + value is dropped.
    await user.selectOptions(screen.getByLabelText("Condition operator"), "isSubmitted");
    expect(quotasState()[0].condition.operator).toBe("isSubmitted");
    expect("value" in quotasState()[0].condition).toBe(false);
    expect(screen.queryByLabelText("Condition value")).toBeNull();
  });

  it("picks an over-quota ending and clears it back to the built-in screen", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add quota/ }));

    const endingSelect = screen.getByLabelText("Over-quota ending");
    await user.selectOptions(endingSelect, "quota_full");
    expect(quotasState()[0].endingId).toBe("quota_full");

    // The first option is the built-in screen (empty value) -> clears endingId.
    await user.selectOptions(endingSelect, "");
    expect(quotasState()[0].endingId).toBeUndefined();
  });

  it("removes a quota", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add quota/ }));
    expect(quotasState()).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: /Delete quota/ }));
    expect(quotasState()).toHaveLength(0);
  });
});
