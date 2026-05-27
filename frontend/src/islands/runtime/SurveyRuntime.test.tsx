// ============================================================================
// Runtime flow tests: drive SurveyRuntime through real progression scenarios
// against a MOCKED RuntimeApi. Asserts branching (engine-driven), required-gating,
// create-on-first-answer, PATCH-on-progress, and finalize-at-end.
// ============================================================================
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PublicResponseHandle, Survey } from "../../lib/types";
import type { RuntimeApi } from "./api";
import { SurveyRuntime } from "./SurveyRuntime";

// --- a recording fake of RuntimeApi (mocks typed to the real method signatures
//     so `.mock.calls[i][j]` is properly typed, not an empty tuple) --- //
function makeFakeApi(survey: Survey) {
  const handle: PublicResponseHandle = { id: "resp-1", finished: false, edit_token: "tok-1" };
  const calls = {
    getSurvey: vi.fn<Parameters<RuntimeApi["getSurvey"]>, Promise<Survey>>(async () => survey),
    createResponse: vi.fn<Parameters<RuntimeApi["createResponse"]>, Promise<PublicResponseHandle>>(
      async () => handle,
    ),
    patchResponse: vi.fn<Parameters<RuntimeApi["patchResponse"]>, Promise<PublicResponseHandle>>(
      async () => handle,
    ),
    finalizeResponse: vi.fn<Parameters<RuntimeApi["finalizeResponse"]>, Promise<PublicResponseHandle>>(
      async () => ({ ...handle, finished: true }),
    ),
  };
  return { api: calls as unknown as RuntimeApi, calls };
}

function npsBranchSurvey(): Survey {
  // NPS detractors (<=6) jump to "reason"; others go to "rating" then end.
  return {
    id: "s1",
    name: "Feedback",
    languages: [{ code: "en", label: "English", default: true }],
    questions: [
      {
        id: "nps",
        type: "nps",
        required: true,
        headline: { default: "How likely are you to recommend us?" },
        config: {},
        logic: [
          {
            id: "r1",
            condition: { leftQuestionId: "nps", operator: "lessThanOrEqual", value: 6 },
            action: { type: "jumpTo", target: "reason" },
          },
        ],
      },
      {
        id: "rating",
        type: "rating",
        required: false,
        headline: { default: "Rate the checkout" },
        config: { range: 5, scale: "star" },
      },
      {
        id: "reason",
        type: "openText",
        required: false,
        headline: { default: "What could we do better?" },
        config: { inputType: "long" },
      },
    ],
    endings: [{ id: "end1", type: "endScreen", headline: { default: "Thanks for your feedback!" } }],
  };
}

describe("SurveyRuntime — happy path + branching", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads the survey and shows the first question", async () => {
    const { api } = makeFakeApi(npsBranchSurvey());
    render(<SurveyRuntime api={api} />);
    expect(await screen.findByText("How likely are you to recommend us?")).toBeInTheDocument();
  });

  it("detractor (NPS<=6) branches to 'reason', creates response on first answer, finalizes at end", async () => {
    const { api, calls } = makeFakeApi(npsBranchSurvey());
    render(<SurveyRuntime api={api} />);

    await screen.findByText("How likely are you to recommend us?");
    // Answer NPS = 3 (detractor)
    await userEvent.click(screen.getByRole("radio", { name: "3" }));
    await userEvent.click(screen.getByRole("button", { name: /next/i }));

    // Response created on first answer with the NPS answer.
    await waitFor(() => expect(calls.createResponse).toHaveBeenCalledTimes(1));
    expect(calls.createResponse.mock.calls[0][0]).toEqual({ nps: 3 });

    // Engine routed to the open-text reason (NOT rating).
    expect(await screen.findByText("What could we do better?")).toBeInTheDocument();

    // Answer reason (optional) and finish -> reaches the ending + finalizes.
    await userEvent.type(screen.getByRole("textbox"), "Slow checkout");
    await userEvent.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() => expect(calls.finalizeResponse).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Thanks for your feedback!")).toBeInTheDocument();
    // The second persist was a PATCH (response already created).
    expect(calls.patchResponse).toHaveBeenCalledTimes(1);
  });

  it("promoter (NPS>=7) advances linearly to 'rating'", async () => {
    const { api } = makeFakeApi(npsBranchSurvey());
    render(<SurveyRuntime api={api} />);

    await screen.findByText("How likely are you to recommend us?");
    await userEvent.click(screen.getByRole("radio", { name: "9" }));
    await userEvent.click(screen.getByRole("button", { name: /next/i }));

    expect(await screen.findByText("Rate the checkout")).toBeInTheDocument();
  });
});

describe("SurveyRuntime — required gating", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("blocks advancing past a required question with no answer", async () => {
    const { api, calls } = makeFakeApi(npsBranchSurvey());
    render(<SurveyRuntime api={api} />);

    await screen.findByText("How likely are you to recommend us?");
    // Click Next without answering the required NPS.
    await userEvent.click(screen.getByRole("button", { name: /next/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/required/i);
    // No response created — nothing persisted.
    expect(calls.createResponse).not.toHaveBeenCalled();
    // Still on the NPS question.
    expect(screen.getByText("How likely are you to recommend us?")).toBeInTheDocument();
  });

  it("enforces a logic-required follow-up (require action) before finishing", async () => {
    // q1==yes => require(email). email is optional statically but required-by-logic.
    const survey: Survey = {
      id: "s2",
      languages: [{ code: "en", label: "English", default: true }],
      questions: [
        {
          id: "interested",
          type: "singleSelect",
          required: true,
          headline: { default: "Interested in a demo?" },
          config: {
            choices: [
              { id: "yes", label: { default: "Yes" } },
              { id: "no", label: { default: "No" } },
            ],
          },
          logic: [
            {
              id: "g1",
              condition: { leftQuestionId: "interested", operator: "equals", value: "yes" },
              action: { type: "require", target: "email" },
            },
          ],
        },
        {
          id: "email",
          type: "openText",
          required: false,
          headline: { default: "Your email" },
          config: { inputType: "short" },
        },
      ],
      endings: [{ id: "end1", type: "endScreen", headline: { default: "Done" } }],
    };
    const { api } = makeFakeApi(survey);
    render(<SurveyRuntime api={api} />);

    await screen.findByText("Interested in a demo?");
    await userEvent.click(screen.getByLabelText("Yes"));
    await userEvent.click(screen.getByRole("button", { name: /next/i }));

    // Now on email, which is required-by-logic. Required marker must show.
    expect(await screen.findByText("Your email")).toBeInTheDocument();
    expect(screen.getByTitle("Required")).toBeInTheDocument();

    // Try to skip it -> blocked.
    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/required/i);
  });
});

describe("SurveyRuntime — back navigation + welcome", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the welcome card when enabled and starts on click", async () => {
    const survey = npsBranchSurvey();
    survey.welcomeCard = { enabled: true, headline: { default: "Welcome!" }, buttonLabel: { default: "Begin" } };
    const { api } = makeFakeApi(survey);
    render(<SurveyRuntime api={api} />);

    expect(await screen.findByText("Welcome!")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Begin" }));
    expect(await screen.findByText("How likely are you to recommend us?")).toBeInTheDocument();
  });

  it("lets the respondent go back to a previous question", async () => {
    const { api } = makeFakeApi(npsBranchSurvey());
    render(<SurveyRuntime api={api} />);

    await screen.findByText("How likely are you to recommend us?");
    await userEvent.click(screen.getByRole("radio", { name: "9" }));
    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByText("Rate the checkout");

    await userEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(await screen.findByText("How likely are you to recommend us?")).toBeInTheDocument();
  });
});

describe("SurveyRuntime — closed / expired / invalid (terminal error states)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function renderWithGetError(status: number, kind: string, retryAfter?: number) {
    const { ApiError } = await import("./api");
    const calls = {
      getSurvey: vi.fn(async () => {
        throw new ApiError(status, kind as never, retryAfter);
      }),
      createResponse: vi.fn(),
      patchResponse: vi.fn(),
      finalizeResponse: vi.fn(),
    };
    render(<SurveyRuntime api={calls as unknown as RuntimeApi} />);
  }

  it("shows the calm 'This survey is closed' card when GET survey 404s (missing/unpublished)", async () => {
    await renderWithGetError(404, "not_found");
    // Calm, non-alarming headline (NOT a red error) + the brand footer.
    expect(await screen.findByText(/this survey is closed/i)).toBeInTheDocument();
    expect(screen.getByText(/Powered by/i)).toBeInTheDocument();
    expect(screen.getByText("OpenSurvey")).toBeInTheDocument();
  });

  it("shows the closed card when the survey is gone/closed (410: auto-closed or link consumed)", async () => {
    await renderWithGetError(410, "gone");
    expect(await screen.findByText(/this survey is closed/i)).toBeInTheDocument();
    expect(screen.getByText(/no longer being collected/i)).toBeInTheDocument();
  });

  it("surfaces a calm rate-limit message (with retry-after) instead of an alarming error", async () => {
    await renderWithGetError(429, "rate_limited", 30);
    expect(await screen.findByText(/just a moment/i)).toBeInTheDocument();
    expect(screen.getByText(/30s/)).toBeInTheDocument();
  });

  it("renders the closed state full-bleed — no in-flow white question card", async () => {
    await renderWithGetError(410, "gone");
    await screen.findByText(/this survey is closed/i);
    // The status card uses role=status (calm), not role=alert (alarming).
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("falls back to a closed terminal state when GET throws a non-ApiError (network)", async () => {
    const calls = {
      getSurvey: vi.fn(async () => {
        throw new Error("boom");
      }),
      createResponse: vi.fn(),
      patchResponse: vi.fn(),
      finalizeResponse: vi.fn(),
    };
    render(<SurveyRuntime api={calls as unknown as RuntimeApi} />);
    expect(await screen.findByText(/this survey is unavailable/i)).toBeInTheDocument();
  });

  it("uses an injected initial survey and skips the GET", async () => {
    const { api, calls } = makeFakeApi(npsBranchSurvey());
    render(<SurveyRuntime api={api} initialSurvey={npsBranchSurvey()} />);
    expect(await screen.findByText("How likely are you to recommend us?")).toBeInTheDocument();
    expect(calls.getSurvey).not.toHaveBeenCalled();
  });
});

describe("SurveyRuntime — completed (thank-you) ending", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Drive a single-optional-question survey to its ending, returning the ending used.
  async function finishSurvey(ending?: Partial<import("../../lib/types").Ending>) {
    const survey: Survey = {
      id: "s-end",
      languages: [{ code: "en", label: "English", default: true }],
      questions: [
        {
          id: "q1",
          type: "openText",
          required: false,
          headline: { default: "Any comments?" },
          config: { inputType: "short" },
        },
      ],
      endings: [{ id: "end1", type: "endScreen", ...(ending ?? {}) } as import("../../lib/types").Ending],
    };
    const { api } = makeFakeApi(survey);
    render(<SurveyRuntime api={api} />);
    await screen.findByText("Any comments?");
    await userEvent.click(screen.getByRole("button", { name: /next/i }));
  }

  it("renders the configured ending content (headline, subline) when present", async () => {
    await finishSurvey({
      headline: { default: "All done — you’re a legend! 🙌" },
      subheader: { default: "We’ll use this to make things better." },
    });
    expect(await screen.findByText(/you’re a legend/i)).toBeInTheDocument();
    expect(screen.getByText(/make things better/i)).toBeInTheDocument();
  });

  it("falls back to the default warm thank-you copy when the ending has no content", async () => {
    await finishSurvey();
    expect(await screen.findByText(/thanks for your feedback/i)).toBeInTheDocument();
    expect(screen.getByText(/your response helps us improve/i)).toBeInTheDocument();
  });

  it("renders the optional secondary CTA only when the ending configures a label + link", async () => {
    await finishSurvey({
      headline: { default: "Thanks!" },
      buttonLabel: { default: "Visit our website" },
      buttonLink: "https://example.com",
    });
    const cta = await screen.findByRole("link", { name: /visit our website/i });
    expect(cta).toHaveAttribute("href", "https://example.com");
  });

  it("omits the CTA when only a label (no link) is configured", async () => {
    await finishSurvey({ headline: { default: "Thanks!" }, buttonLabel: { default: "Nowhere" } });
    await screen.findByText("Thanks!");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows the 'Powered by OpenSurvey' footer on the thank-you screen", async () => {
    await finishSurvey();
    await screen.findByText(/thanks for your feedback/i);
    expect(screen.getByText(/Powered by/i)).toBeInTheDocument();
    expect(screen.getByText("OpenSurvey")).toBeInTheDocument();
  });

  it("redirect endings navigate the browser instead of rendering the thank-you card", async () => {
    const orig = window.location;
    // jsdom: make location.href assignable so we can assert the redirect.
    // @ts-expect-error override read-only location for the test
    delete window.location;
    // @ts-expect-error minimal location stub
    window.location = { href: "" };

    await finishSurvey({ type: "redirect", redirectUrl: "https://example.com/thanks" });
    await waitFor(() => expect(window.location.href).toBe("https://example.com/thanks"));
    // The thank-you headline must NOT appear during a redirect.
    expect(screen.queryByText(/thanks for your feedback/i)).not.toBeInTheDocument();

    // @ts-expect-error restore
    window.location = orig;
  });
});

describe("SurveyRuntime — styling (theming wired from lib/styling)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function styledSurvey(styling: import("../../lib/types").Styling): Survey {
    return {
      id: "s-style",
      name: "Styled",
      languages: [{ code: "en", label: "English", default: true }],
      questions: [
        {
          id: "q1",
          type: "openText",
          required: false,
          headline: { default: "How are you?" },
          config: { inputType: "short" },
        },
      ],
      endings: [{ id: "end1", type: "endScreen", headline: { default: "Done" } }],
      styling,
    };
  }

  it("applies a page background color and renders the logo above the card", async () => {
    const survey = styledSurvey({
      background: { type: "color", value: "#101010" },
      logoUrl: "https://example.com/logo.png",
    });
    const { api } = makeFakeApi(survey);
    const { container } = render(<SurveyRuntime api={api} initialSurvey={survey} />);
    await screen.findByText("How are you?");

    const page = container.querySelector(".min-h-screen") as HTMLElement;
    expect(page).toBeTruthy();
    expect(page.style.backgroundColor).toBe("rgb(16, 16, 16)");

    const logo = container.querySelector('img[src="https://example.com/logo.png"]');
    expect(logo).toBeTruthy();
  });

  it("hides the progress bar when hideProgressBar is set (and shows it otherwise)", async () => {
    const hidden = styledSurvey({ hideProgressBar: true });
    const r1 = render(<SurveyRuntime api={makeFakeApi(hidden).api} initialSurvey={hidden} />);
    await screen.findByText("How are you?");
    expect(r1.queryByRole("progressbar")).not.toBeInTheDocument();
    r1.unmount();

    const shown = styledSurvey({});
    render(<SurveyRuntime api={makeFakeApi(shown).api} initialSurvey={shown} />);
    await screen.findByText("How are you?");
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("applies the configured button color to the primary (Next) button", async () => {
    const survey = styledSurvey({ buttonColor: "#00ff00", buttonTextColor: "#000000" });
    const { api } = makeFakeApi(survey);
    render(<SurveyRuntime api={api} initialSurvey={survey} />);
    await screen.findByText("How are you?");

    const next = screen.getByRole("button", { name: /next/i });
    expect(next.style.backgroundColor).toBe("rgb(0, 255, 0)");
    expect(next.style.color).toBe("rgb(0, 0, 0)");
  });
});

// =========================================================================== //
//  XSS hardening — welcome-card HTML routed through lib/sanitizeHtml           //
// =========================================================================== //
describe("SurveyRuntime — welcome card HTML is sanitized (XSS boundary)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function welcomeSurvey(html: string): Survey {
    const survey = npsBranchSurvey();
    survey.welcomeCard = {
      enabled: true,
      headline: { default: "Welcome!" },
      html: { default: html },
      buttonLabel: { default: "Begin" },
    };
    return survey;
  }

  it("strips <script>/onerror/javascript: from the welcome HTML", async () => {
    const survey = welcomeSurvey(
      'Hi<script>window.__pwned=1</script><img src=x onerror="window.__pwned=2"> ' +
        '<a href="javascript:alert(1)">x</a>',
    );
    const { container } = render(<SurveyRuntime api={makeFakeApi(survey).api} initialSurvey={survey} />);
    await screen.findByText("Welcome!");
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).not.toContain("onerror");
    expect(container.innerHTML).not.toContain("__pwned");
    expect(container.innerHTML).not.toContain("javascript:");
  });

  it("keeps legitimate formatting in the welcome HTML", async () => {
    const survey = welcomeSurvey('Please <b>read</b> the <a href="https://example.com">guide</a>.');
    const { container } = render(<SurveyRuntime api={makeFakeApi(survey).api} initialSurvey={survey} />);
    await screen.findByText("Welcome!");
    expect(container.querySelector("b")?.textContent).toBe("read");
    const a = container.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.com");
    expect(a?.getAttribute("rel")).toContain("noopener");
  });
});

// =========================================================================== //
//  White-label — removeBranding hides the "Powered by OpenSurvey" footer      //
// =========================================================================== //
describe("SurveyRuntime — removeBranding (white-label) hides the footer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function finishToEnding(removeBranding?: boolean) {
    const survey: Survey = {
      id: "s-brand",
      languages: [{ code: "en", label: "English", default: true }],
      questions: [
        {
          id: "q1",
          type: "openText",
          required: false,
          headline: { default: "Any comments?" },
          config: { inputType: "short" },
        },
      ],
      endings: [{ id: "end1", type: "endScreen", headline: { default: "Thanks!" } }],
    };
    render(
      <SurveyRuntime api={makeFakeApi(survey).api} initialSurvey={survey} removeBranding={removeBranding} />,
    );
    await screen.findByText("Any comments?");
    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByText("Thanks!");
  }

  it("shows the footer on the ending screen by default (branding on)", async () => {
    await finishToEnding(undefined);
    expect(screen.getByText("OpenSurvey")).toBeInTheDocument();
  });

  it("hides the footer on the ending screen when removeBranding is true", async () => {
    await finishToEnding(true);
    expect(screen.queryByText("OpenSurvey")).not.toBeInTheDocument();
    expect(screen.queryByText(/Powered by/i)).not.toBeInTheDocument();
  });

  it("hides the footer on the closed/error terminal screen when removeBranding is true", async () => {
    const { ApiError } = await import("./api");
    const calls = {
      getSurvey: vi.fn(async () => {
        throw new ApiError(410, "gone" as never);
      }),
      createResponse: vi.fn(),
      patchResponse: vi.fn(),
      finalizeResponse: vi.fn(),
    };
    render(<SurveyRuntime api={calls as unknown as RuntimeApi} removeBranding={true} />);
    await screen.findByText(/this survey is closed/i);
    expect(screen.queryByText("OpenSurvey")).not.toBeInTheDocument();
  });
});
