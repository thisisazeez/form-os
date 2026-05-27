// ============================================================================
// Logic tab component tests (BuilderApp-level, mocked API).
//
// Drives the real BuilderApp so the tests cover the WHOLE flow the user sees:
//   • Build · Logic tab switcher (and that B1's toolbar survives on both tabs).
//   • Add a logic rule → a rule card appears (empty-state replaced).
//   • Edit a rule's condition (operator/value) + action → reflected in the
//     SurveyWrite payload INSIDE question.logic[] when Saved.
//   • Delete a rule → gone.
//   • The saved payload passes the SHARED validateSurvey (no 400 on logic).
//   • Negative: a deliberately backward jump is caught by validateSurvey.
// ============================================================================
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { validateSurvey } from "../../../lib/logic";
import type { Question, Survey } from "../../../lib/types";
import { BuilderApp } from "../BuilderApp";
import type { SavedSurvey } from "../api";

function makeApiStub(saveImpl: (body: Record<string, unknown>) => Promise<SavedSurvey>) {
  return {
    saveSurvey: vi.fn(saveImpl),
    publishSurvey: vi.fn(
      async () => ({ id: "sv-1", status: "published" }) as unknown as SavedSurvey,
    ),
  } as unknown as import("../api").BuilderApi & {
    saveSurvey: ReturnType<typeof vi.fn>;
    publishSurvey: ReturnType<typeof vi.fn>;
  };
}

const echo = async (body: Record<string, unknown>): Promise<SavedSurvey> =>
  ({ id: "sv-1", ...body } as unknown as SavedSurvey);

const ORG = "acme";

function q(id: string, type: Question["type"], extra: Partial<Question> = {}): Question {
  const base: Question = { id, type, headline: { default: id }, config: {}, ...extra };
  if (type === "singleSelect") {
    base.config = {
      choices: [
        { id: "yes", label: { default: "Yes" } },
        { id: "no", label: { default: "No" } },
      ],
    };
  } else if (type === "rating") {
    base.config = { range: 5, scale: "star" };
  }
  return base;
}

function threeQuestionSurvey(): Survey {
  return {
    name: "Test survey",
    questions: [q("q1", "openText"), q("q2", "nps"), q("q3", "openText")],
    languages: [{ code: "en", default: true }],
    endings: [{ id: "end1", type: "endScreen", headline: { default: "Thanks" } }],
  };
}

/** Switch to the Logic tab. */
async function gotoLogic(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: "Logic" }));
}

// --------------------------------------------------------------------------- //
//  Tab switcher                                                               //
// --------------------------------------------------------------------------- //
describe("Builder tabs — Build · Logic", () => {
  it("renders both tabs; Build is active by default and shows the question palette", () => {
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);

    expect(screen.getByRole("tab", { name: "Build" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Logic" })).toHaveAttribute("aria-selected", "false");
    // Build pane content present.
    expect(screen.getByText("Add question")).toBeInTheDocument();
  });

  it("switches to Logic and back, keeping B1's toolbar intact on both tabs", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);

    // Toolbar present on Build.
    expect(screen.getByRole("button", { name: /^Save$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Publish$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Discard changes/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to surveys/i })).toBeInTheDocument();
    expect(screen.getByTestId("status-pill")).toBeInTheDocument();

    await gotoLogic(user);

    // Logic view content present; toolbar STILL present (shared across tabs).
    expect(screen.getByRole("heading", { name: /Logic rules/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add logic rule/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Save$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Publish$/ })).toBeInTheDocument();
    expect(screen.getByTestId("status-pill")).toBeInTheDocument();

    // Build pane hidden while on Logic.
    expect(screen.queryByText("Add question")).not.toBeInTheDocument();

    // Back to Build.
    await user.click(screen.getByRole("tab", { name: "Build" }));
    expect(screen.getByText("Add question")).toBeInTheDocument();
  });

  it("shows the empty-logic state when there are no rules", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    expect(
      screen.getByText(/No logic yet — your survey runs top to bottom/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("rule-card")).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
//  Add / edit / delete a rule                                                 //
// --------------------------------------------------------------------------- //
describe("Logic rules — add / edit / delete", () => {
  it("adds a rule: the empty state is replaced by a rule card", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));

    expect(screen.getByTestId("rule-card")).toBeInTheDocument();
    expect(
      screen.queryByText(/No logic yet — your survey runs top to bottom/i),
    ).not.toBeInTheDocument();
    // Adding a rule marks the survey dirty.
    expect(screen.getByTestId("unsaved-hint")).toBeInTheDocument();
  });

  it("adding a rule then Saving PUTs it inside question.logic[] and passes validateSurvey", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    expect(api.saveSurvey).toHaveBeenCalledTimes(1);
    const body = api.saveSurvey.mock.calls[0][0] as { questions: Question[] };
    // The rule lives on q1 (its condition refs q1 → host q1).
    const q1 = body.questions.find((x) => x.id === "q1")!;
    expect(q1.logic).toHaveLength(1);
    expect(q1.logic![0].action).toEqual({ type: "jumpTo", target: "q2" });

    // The saved payload passes the SHARED validator (server won't 400 on logic).
    expect(validateSurvey(body as unknown as Survey).ok).toBe(true);

    // "Saved" confirmation shown.
    expect(await screen.findByText(/^Saved$/)).toBeInTheDocument();
  });

  it("edits a condition operator + value and reflects it in the saved payload", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));
    const card = within(screen.getByTestId("rule-card"));

    // Target the ending FIRST — an ending is an always-valid forward target,
    // immune to the host migrating forward when we change the condition below.
    // (Re-hosting onto a question that lies at/after the target is its own hazard,
    // covered by the dedicated backward-jump test — this test isolates the
    // operator/value round-trip.)
    expect(card.getByRole("option", { name: /Ending: Thanks/i })).toBeInTheDocument();
    await user.selectOptions(card.getByLabelText("Action target"), "end1");

    // Point the condition at q2 (NPS), pick "is less than", value 6.
    await user.selectOptions(card.getByLabelText("Condition question"), "q2");
    await user.selectOptions(card.getByLabelText("Condition operator"), "lessThan");
    const valueInput = card.getByLabelText("Condition value");
    await user.clear(valueInput);
    await user.type(valueInput, "6");

    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    const body = api.saveSurvey.mock.calls[0][0] as { questions: Question[] };
    // Condition now refs q2 → the rule re-hosts onto q2.
    const q2 = body.questions.find((x) => x.id === "q2")!;
    expect(q2.logic).toHaveLength(1);
    const leaf = q2.logic![0].condition as { leftQuestionId: string; operator: string; value: number };
    expect(leaf.leftQuestionId).toBe("q2");
    expect(leaf.operator).toBe("lessThan");
    expect(leaf.value).toBe(6);
    // The action still targets the ending (unaffected by the re-host) — still valid.
    expect(q2.logic![0].action).toEqual({ type: "jumpTo", target: "end1" });
    expect(validateSurvey(body as unknown as Survey).ok).toBe(true);
  });

  it("changes the action to End survey and persists it", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));
    const card = within(screen.getByTestId("rule-card"));

    await user.selectOptions(card.getByLabelText("Action"), "endSurvey");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    const body = api.saveSurvey.mock.calls[0][0] as { questions: Question[] };
    const q1 = body.questions.find((x) => x.id === "q1")!;
    expect(q1.logic![0].action.type).toBe("endSurvey");
    expect(validateSurvey(body as unknown as Survey).ok).toBe(true);
  });

  it("changes the action to Skip and persists a forward skip target", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));
    const card = within(screen.getByTestId("rule-card"));

    // Rule hosted on q1. Switch to Skip → defaults to skipping q2 (first after host).
    await user.selectOptions(card.getByLabelText("Action"), "skip");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    const body = api.saveSurvey.mock.calls[0][0] as { questions: Question[] };
    const q1 = body.questions.find((x) => x.id === "q1")!;
    const action = q1.logic![0].action as { type: string; target: string | string[] };
    expect(action.type).toBe("skip");
    // single target stored as a scalar (schema-valid: questionId OR array)
    expect(action.target).toBe("q2");
    expect(validateSurvey(body as unknown as Survey).ok).toBe(true);
  });

  it("changes the action to Make required and targets a later question", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));
    const card = within(screen.getByTestId("rule-card"));

    await user.selectOptions(card.getByLabelText("Action"), "require");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    const body = api.saveSurvey.mock.calls[0][0] as { questions: Question[] };
    const q1 = body.questions.find((x) => x.id === "q1")!;
    expect(q1.logic![0].action).toEqual({ type: "require", target: "q2" });
    expect(validateSurvey(body as unknown as Survey).ok).toBe(true);
  });

  it("reorders two rules on the same host (precedence) via the up arrow", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    // Two rules both hosted on q1 (conditions ref q1).
    const initial: Survey = {
      ...threeQuestionSurvey(),
      questions: [
        {
          ...q("q1", "openText"),
          logic: [
            {
              id: "ra",
              condition: { leftQuestionId: "q1", operator: "isSubmitted" },
              action: { type: "jumpTo", target: "q2" },
            },
            {
              id: "rb",
              condition: { leftQuestionId: "q1", operator: "isSubmitted" },
              action: { type: "jumpTo", target: "q3" },
            },
          ],
        },
        q("q2", "nps"),
        q("q3", "openText"),
      ],
    };
    render(<BuilderApp api={api} initialSurvey={initial} orgSlug={ORG} />);
    await gotoLogic(user);

    // Two rule cards; move rule 2 up.
    expect(screen.getAllByTestId("rule-card")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: /Move rule 2 up/i }));

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    const body = api.saveSurvey.mock.calls[0][0] as { questions: Question[] };
    const q1 = body.questions.find((x) => x.id === "q1")!;
    // Order swapped within q1.logic[] (precedence changed).
    expect(q1.logic!.map((r) => r.id)).toEqual(["rb", "ra"]);
    expect(validateSurvey(body as unknown as Survey).ok).toBe(true);
  });

  it("adds a second condition (AND) and keeps the survey valid", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));
    const card = () => within(screen.getByTestId("rule-card"));

    await user.click(card().getByRole("button", { name: /Add condition/i }));

    // Two condition rows now (two "Condition question" selects).
    expect(card().getAllByLabelText("Condition question")).toHaveLength(2);
    // A connector control appears for the 2nd condition.
    expect(card().getByLabelText("Condition connector")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    const body = api.saveSurvey.mock.calls[0][0] as { questions: Question[] };
    expect(validateSurvey(body as unknown as Survey).ok).toBe(true);
  });

  it("deletes a rule: the card is removed and the empty state returns", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));
    expect(screen.getByTestId("rule-card")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Delete rule 1/i }));

    expect(screen.queryByTestId("rule-card")).not.toBeInTheDocument();
    expect(
      screen.getByText(/No logic yet — your survey runs top to bottom/i),
    ).toBeInTheDocument();

    // Saving now persists an empty logic[] on q1 (rule gone) → still valid.
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    const body = api.saveSurvey.mock.calls[0][0] as { questions: Question[] };
    const q1 = body.questions.find((x) => x.id === "q1")!;
    expect(q1.logic ?? []).toHaveLength(0);
    expect(validateSurvey(body as unknown as Survey).ok).toBe(true);
  });
});

// --------------------------------------------------------------------------- //
//  Inline validation + negative case                                          //
// --------------------------------------------------------------------------- //
describe("Logic rules — validation", () => {
  it("surfaces an inline backward-jump notice when a re-host invalidates the target", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={threeQuestionSurvey()} orgSlug={ORG} />);
    await gotoLogic(user);

    await user.click(screen.getByRole("button", { name: /Add logic rule/i }));
    const card = () => within(screen.getByTestId("rule-card"));

    // Default: rule on q1, jumpTo q2. Point the condition at q3 → host migrates to
    // q3, but the action still targets q2 (now BEFORE the host) → backward jump.
    await user.selectOptions(card().getByLabelText("Condition question"), "q3");

    // Inline issue shows on the card.
    expect(card().getByTestId("rule-issues")).toBeInTheDocument();
    expect(card().getByText(/Target must come after the conditions/i)).toBeInTheDocument();

    // And the shared validator (run by Save) would reject it.
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(await screen.findByText(/Couldn’t save the survey/i)).toBeInTheDocument();
    expect(api.saveSurvey).not.toHaveBeenCalled(); // client-side validateSurvey gate blocks the PUT
  });

  it("a backward-jump rule is rejected by the shared validateSurvey (negative)", () => {
    // Construct directly: rule on q2 jumping back to q1.
    const s: Survey = {
      name: "T",
      questions: [
        q("q1", "openText"),
        {
          ...q("q2", "nps"),
          logic: [
            {
              id: "bad",
              condition: { leftQuestionId: "q2", operator: "lessThan", value: 6 },
              action: { type: "jumpTo", target: "q1" }, // backward
            },
          ],
        },
      ],
      languages: [{ code: "en", default: true }],
      endings: [],
    };
    const result = validateSurvey(s);
    expect(result.ok).toBe(false);
    expect(!result.ok && result.errors.some((e) => e.code === "backward_jump")).toBe(true);
  });
});
