// ============================================================================
// FollowUpsPanel component tests (vitest + testing-library).
//
// Drives the builder's 4th-tab editor: add/remove follow-ups, toggle the trigger
// (always-on-finish ↔ a simple condition), switch the recipient form, and edit
// subject/body. Asserts on the controlled survey state the panel emits.
// ============================================================================
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type { Survey } from "../../lib/types";
import { FollowUpsPanel } from "./FollowUpsPanel";

function baseSurvey(): Survey {
  return {
    name: "S",
    questions: [
      { id: "interested", type: "singleSelect", headline: { default: "Interested?" }, config: { choices: [] } },
      { id: "email", type: "contactInfo", headline: { default: "Email?" }, config: { fields: {} } },
    ],
    languages: [{ code: "en", default: true }],
  };
}

function Harness({ initial, lang = "default" }: { initial: Survey; lang?: string }) {
  const [survey, setSurvey] = useState(initial);
  const defaultLang = survey.languages.find((l) => l.default)?.code ?? "default";
  return (
    <>
      <FollowUpsPanel survey={survey} onChange={setSurvey} lang={lang} defaultLang={defaultLang} />
      <pre data-testid="state">{JSON.stringify(survey.followUps ?? [])}</pre>
    </>
  );
}

function followUpsState() {
  return JSON.parse(screen.getByTestId("state").textContent || "[]");
}

describe("FollowUpsPanel", () => {
  it("starts empty with an empty-state hint", () => {
    render(<Harness initial={baseSurvey()} />);
    expect(screen.getByText(/No follow-ups yet/)).toBeInTheDocument();
    expect(followUpsState()).toEqual([]);
  });

  it("adds a follow-up with schema-shaped defaults", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));
    const state = followUpsState();
    expect(state).toHaveLength(1);
    expect(state[0].trigger.type).toBe("responseFinished");
    expect(state[0].action.type).toBe("sendEmail");
    expect(state[0].action.recipient).toEqual({ type: "fixedEmail", email: "" });
  });

  it("shows a required hint while the fixedEmail recipient is blank, hides it once filled", async () => {
    // Regression: a blank recipient email makes the server reject Save with a cryptic
    // "fixedEmail recipient '' is not a valid email" — surface it inline up front instead.
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));
    expect(screen.getByText(/required before this follow-up can be saved/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Recipient email"), "team@example.com");
    expect(
      screen.queryByText(/required before this follow-up can be saved/i),
    ).not.toBeInTheDocument();
  });

  it("removes a follow-up", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));
    await user.click(screen.getByRole("button", { name: /Delete follow-up/ }));
    expect(followUpsState()).toEqual([]);
    expect(screen.getByText(/No follow-ups yet/)).toBeInTheDocument();
  });

  it("toggles the trigger between always and conditional", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));

    // Switch to conditional -> a condition leaf appears + is stored.
    await user.selectOptions(screen.getByLabelText("Trigger"), "conditional");
    let fu = followUpsState()[0];
    expect(fu.trigger.condition).toBeTruthy();
    expect(fu.trigger.condition.leftQuestionId).toBe("interested");

    // Switch back to always -> condition cleared.
    await user.selectOptions(screen.getByLabelText("Trigger"), "always");
    fu = followUpsState()[0];
    expect(fu.trigger.condition).toBeUndefined();
  });

  it("edits a simple condition (question + operator + value)", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));
    await user.selectOptions(screen.getByLabelText("Trigger"), "conditional");

    await user.selectOptions(screen.getByLabelText("Condition operator"), "equals");
    await user.type(screen.getByLabelText("Condition value"), "yes");
    const fu = followUpsState()[0];
    expect(fu.trigger.condition).toMatchObject({
      leftQuestionId: "interested",
      operator: "equals",
      value: "yes",
    });
  });

  it("presence operators drop the value field", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));
    await user.selectOptions(screen.getByLabelText("Trigger"), "conditional");
    await user.selectOptions(screen.getByLabelText("Condition operator"), "isSubmitted");
    expect(screen.queryByLabelText("Condition value")).not.toBeInTheDocument();
    const fu = followUpsState()[0];
    expect(fu.trigger.condition.operator).toBe("isSubmitted");
    expect("value" in fu.trigger.condition).toBe(false);
  });

  it("switches the recipient form and edits a fixed email", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));

    // Default seed is fixedEmail; type an address.
    await user.type(screen.getByLabelText("Recipient email"), "ops@acme.com");
    expect(followUpsState()[0].action.recipient).toEqual({
      type: "fixedEmail",
      email: "ops@acme.com",
    });

    // Switch to responseField -> a question selector appears, seeded with the first question.
    await user.selectOptions(screen.getByLabelText("Recipient type"), "responseField");
    expect(followUpsState()[0].action.recipient).toEqual({
      type: "responseField",
      questionId: "interested",
    });

    // Switch to contact -> no extra input, recipient is {type:'contact'}.
    await user.selectOptions(screen.getByLabelText("Recipient type"), "contact");
    expect(followUpsState()[0].action.recipient).toEqual({ type: "contact" });
  });

  it("edits subject and body (default language)", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));

    const subject = screen.getByLabelText("Email subject");
    await user.clear(subject);
    await user.type(subject, "Thanks!");
    expect(followUpsState()[0].action.subject.default).toBe("Thanks!");

    const body = screen.getByLabelText("Email body");
    await user.clear(body);
    await user.type(body, "<p>Hi</p>");
    expect(followUpsState()[0].action.body.default).toBe("<p>Hi</p>");
  });

  it("names a follow-up", async () => {
    const user = userEvent.setup();
    render(<Harness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));
    await user.type(screen.getByLabelText("Follow-up name"), "Ops ping");
    expect(followUpsState()[0].name).toBe("Ops ping");
  });

  it("multi-language: subject writes the active lang key, preserving default", async () => {
    const user = userEvent.setup();
    const s: Survey = {
      ...baseSurvey(),
      languages: [
        { code: "en", default: true },
        { code: "fr", default: false },
      ],
    };
    render(<Harness initial={s} lang="fr" />);
    await user.click(screen.getByRole("button", { name: /Add follow-up/ }));
    const subject = screen.getByLabelText("Email subject");
    await user.clear(subject);
    await user.type(subject, "Bonjour");
    const fu = followUpsState()[0];
    expect(fu.action.subject.fr).toBe("Bonjour");
    // The seeded default key is preserved.
    expect(fu.action.subject.default).toBeTruthy();
  });
});
