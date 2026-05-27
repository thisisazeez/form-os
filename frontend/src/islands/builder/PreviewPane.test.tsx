// ============================================================================
// PreviewPane tests — proves the live preview REUSES the shared logic engine
// (nextStep + requiredQuestionIds) and answer-gating, not just the renderers.
// ============================================================================
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Survey } from "../../lib/types";
import { PreviewPane } from "./PreviewPane";

function survey(questions: Survey["questions"]): Survey {
  return { name: "S", questions, languages: [{ code: "en", default: true }] };
}

describe("PreviewPane — shared engine reuse", () => {
  it("gates a required question: blocks advance and shows the required message", async () => {
    const user = userEvent.setup();
    const s = survey([
      { id: "q1", type: "openText", headline: { default: "Name" }, required: true, config: {} },
      { id: "q2", type: "openText", headline: { default: "Second" }, config: {} },
    ]);
    render(<PreviewPane survey={s} />);

    // On the first (required) question; advancing without an answer is blocked.
    expect(screen.getByRole("heading", { level: 2, name: /Name/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));

    // Same validateAnswer message the runtime uses.
    expect(screen.getByText(/This question is required\./i)).toBeInTheDocument();
    // Still on question 1.
    expect(screen.getByRole("heading", { level: 2, name: /Name/ })).toBeInTheDocument();
  });

  it("advances to the next question once the required answer is provided", async () => {
    const user = userEvent.setup();
    const s = survey([
      { id: "q1", type: "openText", headline: { default: "Name" }, required: true, config: {} },
      { id: "q2", type: "openText", headline: { default: "Second" }, config: {} },
    ]);
    render(<PreviewPane survey={s} />);

    await user.type(screen.getByRole("textbox"), "Ada");
    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(screen.getByRole("heading", { level: 2, name: /Second/ })).toBeInTheDocument();
  });

  it("follows a jumpTo branch via the shared nextStep engine", async () => {
    const user = userEvent.setup();
    // q1 jumps to q3 when answered "yes"; q2 is skipped.
    const s = survey([
      {
        id: "q1",
        type: "singleSelect",
        headline: { default: "Branch?" },
        config: {
          choices: [
            { id: "yes", label: { default: "Yes" } },
            { id: "no", label: { default: "No" } },
          ],
          display: "list",
        },
        logic: [
          {
            id: "r1",
            condition: { leftQuestionId: "q1", operator: "equals", value: "yes" },
            action: { type: "jumpTo", target: "q3" },
          },
        ],
      },
      { id: "q2", type: "openText", headline: { default: "Skipped" }, config: {} },
      { id: "q3", type: "openText", headline: { default: "Target" }, config: {} },
    ]);
    render(<PreviewPane survey={s} />);

    await user.click(screen.getByRole("radio", { name: "Yes" }));
    await user.click(screen.getByRole("button", { name: "Next" }));

    // Engine jumped past q2 to q3.
    expect(screen.getByRole("heading", { level: 2, name: /Target/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: /Skipped/ })).not.toBeInTheDocument();
  });

  it("renders a recall token in a later headline with the live prior answer", async () => {
    const user = userEvent.setup();
    // q2's headline recalls q1's answer; preview must resolve it live (shared QuestionRenderer).
    const s = survey([
      { id: "q1", type: "openText", headline: { default: "Your name?" }, config: {} },
      {
        id: "q2",
        type: "openText",
        headline: { default: "Hi #recall:q1/fallback:there#, anything else?" },
        config: {},
      },
    ]);
    render(<PreviewPane survey={s} />);

    await user.type(screen.getByRole("textbox"), "Ada");
    await user.click(screen.getByRole("button", { name: "Next" }));

    // The token resolved to the live q1 answer.
    expect(
      screen.getByRole("heading", { level: 2, name: /Hi Ada, anything else\?/ }),
    ).toBeInTheDocument();
  });

  it("renders a recall token's fallback when the source is unanswered (hidden field in preview)", async () => {
    // Hidden fields are unanswered in the builder preview (no URL capture), so a recall of
    // one shows its fallback — verifying the no-live-value path renders cleanly.
    const s: Survey = {
      name: "S",
      languages: [{ code: "en", default: true }],
      hiddenFields: [{ id: "userId", name: "User ID" }],
      questions: [
        {
          id: "q1",
          type: "openText",
          headline: { default: "Welcome #recall:userId/fallback:guest#" },
          config: {},
        },
      ],
    };
    render(<PreviewPane survey={s} />);
    expect(
      screen.getByRole("heading", { level: 2, name: /Welcome guest/ }),
    ).toBeInTheDocument();
  });

  it("Restart resets the preview to the first question", async () => {
    const user = userEvent.setup();
    const s = survey([
      { id: "q1", type: "openText", headline: { default: "One" }, config: {} },
      { id: "q2", type: "openText", headline: { default: "Two" }, config: {} },
    ]);
    render(<PreviewPane survey={s} />);

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("heading", { level: 2, name: /Two/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Restart" }));
    expect(screen.getByRole("heading", { level: 2, name: /One/ })).toBeInTheDocument();
  });
});

describe("PreviewPane — welcome + ending steps", () => {
  it("renders the welcome step first when the welcome card is enabled", () => {
    const s = survey([{ id: "q1", type: "openText", headline: { default: "Q1" }, config: {} }]);
    s.welcomeCard = {
      enabled: true,
      headline: { default: "Welcome aboard" },
      buttonLabel: { default: "Begin" },
    };
    render(<PreviewPane survey={s} />);
    // The welcome card headline is an <h1> (vs. the question's <h2>).
    expect(screen.getByRole("heading", { level: 1, name: /Welcome aboard/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Begin" })).toBeInTheDocument();
    // The first question isn't shown yet — the welcome card precedes it.
    expect(screen.queryByRole("heading", { level: 2, name: /Q1/ })).not.toBeInTheDocument();
  });

  it("re-seats to the welcome step when the card is toggled on (structural reset)", () => {
    const base = survey([{ id: "q1", type: "openText", headline: { default: "Q1" }, config: {} }]);
    const { rerender } = render(<PreviewPane survey={base} />);
    // Initially the first question shows (no welcome card).
    expect(screen.getByRole("heading", { level: 2, name: /Q1/ })).toBeInTheDocument();

    // Toggle the welcome card on via a re-render with a fresh survey (mirrors the builder).
    const withWelcome: Survey = {
      ...base,
      welcomeCard: { enabled: true, headline: { default: "Hello!" } },
    };
    rerender(<PreviewPane survey={withWelcome} />);
    expect(screen.getByRole("heading", { level: 1, name: /Hello!/ })).toBeInTheDocument();
  });

  it("advances from the welcome step to the first question on Start", async () => {
    const user = userEvent.setup();
    const s = survey([{ id: "q1", type: "openText", headline: { default: "First Q" }, config: {} }]);
    s.welcomeCard = { enabled: true, headline: { default: "Greetings" } };
    render(<PreviewPane survey={s} />);
    // Default button label is "Start" when none is configured.
    await user.click(screen.getByRole("button", { name: "Start" }));
    expect(screen.getByRole("heading", { level: 2, name: /First Q/ })).toBeInTheDocument();
  });

  it("renders a configured ending's content after the last question", async () => {
    const user = userEvent.setup();
    const s = survey([{ id: "q1", type: "openText", headline: { default: "Q1" }, config: {} }]);
    s.endings = [{ id: "e1", type: "endScreen", headline: { default: "Custom thanks" } }];
    render(<PreviewPane survey={s} />);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("heading", { level: 1, name: /Custom thanks/ })).toBeInTheDocument();
  });
});

// =========================================================================== //
//  XSS hardening — welcome-card HTML routed through lib/sanitizeHtml           //
//  (mirrors the runtime; the builder preview must share the same XSS boundary) //
// =========================================================================== //
describe("PreviewPane — welcome card HTML is sanitized (XSS boundary)", () => {
  function welcomeSurvey(html: string): Survey {
    const s = survey([{ id: "q1", type: "openText", headline: { default: "Q1" }, config: {} }]);
    s.welcomeCard = {
      enabled: true,
      headline: { default: "Welcome aboard" },
      html: { default: html },
      buttonLabel: { default: "Begin" },
    };
    return s;
  }

  it("strips <script>/onerror/javascript: from the welcome HTML", () => {
    const { container } = render(
      <PreviewPane
        survey={welcomeSurvey(
          'Hi<script>window.__pwned=1</script><img src=x onerror="window.__pwned=2"> ' +
            '<a href="javascript:alert(1)">x</a>',
        )}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).not.toContain("onerror");
    expect(container.innerHTML).not.toContain("__pwned");
    expect(container.innerHTML).not.toContain("javascript:");
  });

  it("keeps legitimate formatting in the welcome HTML", () => {
    const { container } = render(
      <PreviewPane survey={welcomeSurvey('Please <b>read</b> the <a href="https://example.com">guide</a>.')} />,
    );
    expect(container.querySelector("b")?.textContent).toBe("read");
    const a = container.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.com");
    expect(a?.getAttribute("rel")).toContain("noopener");
  });
});
