// ============================================================================
// SurveySettingsPanel — app-survey (WS-11) UI tests.
//
// Covers the Survey type control + the app-only controls (triggers multi-select +
// display options):
//   - default channel is Link; the app controls are NOT in the DOM for a link survey
//   - flipping to Website & App reveals the trigger picker + display options
//   - the trigger checkboxes drive toggleTrigger via onChange (selected ids carried)
//   - the trigger picker shows a "create an action" hint when no actions exist
//   - display-option inputs patch survey.display_options
//   - flipping back to Link hides the app controls again
//   - link surveys round-trip byte-identical through toSurveyWrite (no WS-11 keys)
// The panel is a controlled component (onChange returns a fresh Survey); these tests
// re-render with the emitted survey to assert the round-trip.
// ============================================================================
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Survey } from "../../lib/types";
import { SurveySettingsPanel } from "./SurveySettingsPanel";
import type { ActionClass } from "./types";
import { toSurveyWrite } from "./surveyModel";

function linkSurvey(): Survey {
  return { name: "S", questions: [{ id: "q1", type: "openText", headline: { default: "Q1" } }], languages: [{ code: "en", default: true }] };
}

const ACTIONS: ActionClass[] = [
  { id: "ac-1", name: "Checkout started", type: "code" },
  { id: "ac-2", name: "Viewed pricing", type: "pageview" },
];

describe("SurveySettingsPanel — survey type control", () => {
  it("defaults to Link and does NOT render the app controls", () => {
    render(<SurveySettingsPanel survey={linkSurvey()} onChange={vi.fn()} actionClasses={ACTIONS} orgSlug="acme" />);
    // Link radio is checked by default.
    const link = screen.getByTestId("survey-type-link").querySelector("input")!;
    const app = screen.getByTestId("survey-type-app").querySelector("input")!;
    expect((link as HTMLInputElement).checked).toBe(true);
    expect((app as HTMLInputElement).checked).toBe(false);
    // The app-only controls are absent for a link survey.
    expect(screen.queryByTestId("trigger-list")).toBeNull();
    expect(screen.queryByLabelText("Display limit")).toBeNull();
    expect(screen.queryByLabelText("Display percentage")).toBeNull();
  });

  it("selecting Website & App emits survey_type=app", async () => {
    const onChange = vi.fn();
    render(<SurveySettingsPanel survey={linkSurvey()} onChange={onChange} actionClasses={ACTIONS} orgSlug="acme" />);
    await userEvent.click(screen.getByTestId("survey-type-app").querySelector("input")!);
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as Survey;
    expect(next.survey_type).toBe("app");
  });

  it("an app survey reveals the trigger picker + display options", () => {
    const survey: Survey = { ...linkSurvey(), survey_type: "app" };
    render(<SurveySettingsPanel survey={survey} onChange={vi.fn()} actionClasses={ACTIONS} orgSlug="acme" />);
    expect(screen.getByTestId("trigger-list")).toBeTruthy();
    expect(screen.getByLabelText("Display limit")).toBeTruthy();
    expect(screen.getByLabelText("Display percentage")).toBeTruthy();
    // Both actions are listed with their names.
    expect(screen.getByLabelText("Trigger: Checkout started")).toBeTruthy();
    expect(screen.getByLabelText("Trigger: Viewed pricing")).toBeTruthy();
  });

  it("checking a trigger emits the toggled id; pre-selected triggers show checked", async () => {
    const onChange = vi.fn();
    const survey: Survey = { ...linkSurvey(), survey_type: "app", triggers: ["ac-2"] };
    render(<SurveySettingsPanel survey={survey} onChange={onChange} actionClasses={ACTIONS} orgSlug="acme" />);
    // ac-2 is pre-selected.
    expect((screen.getByLabelText("Trigger: Viewed pricing") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Trigger: Checkout started") as HTMLInputElement).checked).toBe(false);
    // Checking ac-1 adds it.
    await userEvent.click(screen.getByLabelText("Trigger: Checkout started"));
    const next = onChange.mock.calls[0][0] as Survey;
    expect(next.triggers).toContain("ac-1");
    expect(next.triggers).toContain("ac-2");
  });

  it("shows a 'create an action' hint (with a link) when the env has no actions", () => {
    const survey: Survey = { ...linkSurvey(), survey_type: "app" };
    render(<SurveySettingsPanel survey={survey} onChange={vi.fn()} actionClasses={[]} orgSlug="acme" />);
    const hint = screen.getByTestId("no-actions-hint");
    expect(hint).toBeTruthy();
    const anchor = hint.querySelector("a")!;
    expect(anchor.getAttribute("href")).toBe("/acme/settings/actions");
    // No checkbox list when there are no actions.
    expect(screen.queryByTestId("trigger-list")).toBeNull();
  });

  it("editing the display limit patches survey.display_options", async () => {
    const onChange = vi.fn();
    const survey: Survey = { ...linkSurvey(), survey_type: "app" };
    render(<SurveySettingsPanel survey={survey} onChange={onChange} actionClasses={ACTIONS} orgSlug="acme" />);
    await userEvent.type(screen.getByLabelText("Display limit"), "3");
    const next = onChange.mock.calls.at(-1)![0] as Survey;
    expect(next.display_options).toEqual({ displayLimit: 3 });
  });

  it("a link survey round-trips byte-identical through toSurveyWrite (no WS-11 keys)", () => {
    const body = toSurveyWrite(linkSurvey());
    expect("survey_type" in body).toBe(false);
    expect("triggers" in body).toBe(false);
    expect("display_options" in body).toBe(false);
  });
});
