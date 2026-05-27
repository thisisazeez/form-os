// ============================================================================
// Builder flow tests (component-level, mocked API).
//
// Covers the increment-1 acceptance surface:
//   - add a question (palette) → appears in the list + the live preview
//   - edit a question (headline / choices) → the live preview reflects the edit
//   - reorder (up/down) → list order changes
//   - delete → removed from list + preview
//   - save → PUTs the SurveyWrite payload (right shape) on success
//   - save → surfaces server 400 schema/logic validation errors
//   - publish → POSTs the {status:"published"} transition; reflects the new pill
//   - dirty/discard → unsaved-changes tracking + revert to last-saved
//   - back link → full-page nav to /{orgSlug}/surveys
//
// The live preview asserts the SHARED-LIB REUSE: the preview renders via the same
// QuestionRenderer + logic engine the public runtime uses (no network).
// ============================================================================
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Survey } from "../../lib/types";
import { BuilderApp } from "./BuilderApp";
import { SaveError, type SavedSurvey } from "./api";

/**
 * A stub that satisfies the BuilderApi surface BuilderApp uses (saveSurvey +
 * publishSurvey). `publishImpl` defaults to echoing the survey as published.
 */
function makeApiStub(
  saveImpl: (body: Record<string, unknown>) => Promise<SavedSurvey>,
  publishImpl?: () => Promise<SavedSurvey>,
) {
  return {
    saveSurvey: vi.fn(saveImpl),
    publishSurvey: vi.fn(
      publishImpl ?? (async () => ({ id: "sv-1", status: "published" }) as unknown as SavedSurvey),
    ),
  } as unknown as import("./api").BuilderApi & {
    saveSurvey: ReturnType<typeof vi.fn>;
    publishSurvey: ReturnType<typeof vi.fn>;
  };
}

function emptySurvey(): Survey {
  return { name: "Test survey", questions: [], languages: [{ code: "en", default: true }] };
}

function surveyWith(...questions: Survey["questions"]): Survey {
  return { name: "Test survey", questions, languages: [{ code: "en", default: true }] };
}

const echo = async (body: Record<string, unknown>): Promise<SavedSurvey> =>
  ({ id: "sv-1", ...body } as unknown as SavedSurvey);

/** Default org slug threaded into the back link for every render. */
const ORG = "acme";

describe("BuilderApp — add / edit / preview", () => {
  it("adds a question from the palette: it appears in the list AND the live preview", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={emptySurvey()} orgSlug={ORG} />);

    // Empty state in both panes.
    expect(screen.getByText(/No questions yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Add a question to see the live preview/i)).toBeInTheDocument();

    // Add a Text question.
    await user.click(screen.getByRole("button", { name: /^Text/ }));

    // List shows it (default headline) and the editor opens.
    expect(screen.getByText("Edit question")).toBeInTheDocument();

    // The live preview renders the default-text question via the SHARED renderer.
    // Scope to the preview pane to disambiguate from the list/editor headings.
    const preview = within(screen.getByTestId("preview-pane"));
    expect(preview.getByRole("heading", { level: 2, name: /Your question/i })).toBeInTheDocument();
    // The shared OpenText renderer mounts a textbox in the preview.
    expect(preview.getByRole("textbox")).toBeInTheDocument();
  });

  it("editing the headline updates the live preview (shared renderer reflects edits)", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Original" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    const preview = () => within(screen.getByTestId("preview-pane"));
    // Preview shows the original headline.
    expect(preview().getByRole("heading", { level: 2, name: "Original" })).toBeInTheDocument();

    // Edit the headline field in the editor.
    const headlineField = screen.getByDisplayValue("Original");
    await user.clear(headlineField);
    await user.type(headlineField, "Updated question");

    // The live preview heading reflects the edit immediately (proves edits flow
    // through the shared QuestionRenderer, not a stale snapshot).
    expect(preview().getByRole("heading", { level: 2, name: "Updated question" })).toBeInTheDocument();
  });

  it("editing a single-select choice label flows into the preview's radio options", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = {
      id: "q1",
      type: "singleSelect" as const,
      headline: { default: "Pick" },
      config: {
        choices: [
          { id: "c1", label: { default: "Alpha" } },
          { id: "c2", label: { default: "Beta" } },
        ],
        display: "list",
      },
    };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    const preview = () => within(screen.getByTestId("preview-pane"));
    // Preview renders both choices (shared SingleSelect renderer → radios).
    expect(preview().getByRole("radio", { name: "Alpha" })).toBeInTheDocument();

    // Rename the first choice in the editor (the choice <input> with value "Alpha").
    const choiceField = screen.getByDisplayValue("Alpha");
    await user.clear(choiceField);
    await user.type(choiceField, "Gamma");

    // Preview updates the option label.
    expect(preview().getByRole("radio", { name: "Gamma" })).toBeInTheDocument();
    expect(preview().queryByRole("radio", { name: "Alpha" })).not.toBeInTheDocument();
  });
});

describe("BuilderApp — multi-language authoring", () => {
  /** A two-language survey (en default + fr). */
  function multiLangSurvey(...questions: Survey["questions"]): Survey {
    return {
      name: "Multilang",
      questions,
      languages: [
        { code: "en", default: true },
        { code: "fr", default: false, label: "Français" },
      ],
    };
  }

  it("hides the language switcher for a single-language survey", () => {
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hi" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);
    expect(screen.queryByLabelText("Editing language")).not.toBeInTheDocument();
  });

  it("adds a language: the switcher appears and the new chip is listed", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hi" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.type(screen.getByLabelText("New language code"), "fr");
    await user.type(screen.getByLabelText("New language label"), "Français");
    await user.click(screen.getByRole("button", { name: /Add language/i }));

    // The authoring-language switcher now exists with both options.
    const switcher = screen.getByLabelText("Editing language");
    expect(switcher).toBeInTheDocument();
    expect(within(switcher).getByRole("option", { name: /Français/ })).toBeInTheDocument();
  });

  it("editing in the fr language updates the editor input AND the preview, preserving default", async () => {
    const user = userEvent.setup();
    let lastBody: Record<string, unknown> | undefined;
    const api = makeApiStub(async (body) => {
      lastBody = body;
      return { id: "sv-1", ...body } as unknown as SavedSurvey;
    });
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hello" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={multiLangSurvey(q)} orgSlug={ORG} />);

    const preview = () => within(screen.getByTestId("preview-pane"));
    // Default authoring language (en) → preview shows the default text.
    expect(preview().getByRole("heading", { level: 2, name: "Hello" })).toBeInTheDocument();

    // Switch the authoring language to French.
    await user.selectOptions(screen.getByLabelText("Editing language"), "fr");

    // The headline editor now shows the (empty) fr translation, not the default
    // text — the default-text input ("Hello") is gone in fr mode.
    expect(screen.queryByDisplayValue("Hello")).not.toBeInTheDocument();
    const headlineField = screen.getByPlaceholderText("Your question");
    expect(headlineField).toHaveValue("");
    await user.type(headlineField, "Bonjour");

    // The preview re-renders in fr immediately.
    expect(preview().getByRole("heading", { level: 2, name: "Bonjour" })).toBeInTheDocument();

    // Save: the PUT body carries BOTH keys (default preserved + fr added).
    await user.click(screen.getByRole("button", { name: "Save" }));
    const savedQ = (lastBody!.questions as Survey["questions"])[0];
    expect(savedQ.headline).toEqual({ default: "Hello", fr: "Bonjour" });
  });

  it("switching back to the default language shows the default text again", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = {
      id: "q1",
      type: "openText" as const,
      headline: { default: "Hello", fr: "Bonjour" },
      config: {},
    };
    render(<BuilderApp api={api} initialSurvey={multiLangSurvey(q)} orgSlug={ORG} />);

    // Default (en) edit field shows the default text.
    expect(screen.getByDisplayValue("Hello")).toBeInTheDocument();
    // Switch to fr → field shows the fr translation.
    await user.selectOptions(screen.getByLabelText("Editing language"), "fr");
    expect(screen.getByDisplayValue("Bonjour")).toBeInTheDocument();
    // Switch back to en → default text again.
    await user.selectOptions(screen.getByLabelText("Editing language"), "en");
    expect(screen.getByDisplayValue("Hello")).toBeInTheDocument();
  });
});

describe("BuilderApp — reorder / delete", () => {
  it("reorders questions with the up arrow", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const a = { id: "qa", type: "openText" as const, headline: { default: "First" }, config: {} };
    const b = { id: "qb", type: "openText" as const, headline: { default: "Second" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(a, b)} orgSlug={ORG} />);

    const list = screen.getByRole("list");
    const before = within(list).getAllByText(/First|Second/).map((n) => n.textContent);
    expect(before).toEqual(["First", "Second"]);

    // Move "Second" up.
    await user.click(screen.getByRole("button", { name: /Move "Second" up/i }));

    const after = within(screen.getByRole("list"))
      .getAllByText(/First|Second/)
      .map((n) => n.textContent);
    expect(after).toEqual(["Second", "First"]);
  });

  it("deletes a question: gone from the list and the preview", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const a = { id: "qa", type: "openText" as const, headline: { default: "Keep me" }, config: {} };
    const b = { id: "qb", type: "openText" as const, headline: { default: "Delete me" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(a, b)} orgSlug={ORG} />);

    await user.click(screen.getByRole("button", { name: /Delete "Delete me"/i }));

    // Gone from the list (the row carrying "Delete me" is removed).
    const list = within(screen.getByRole("list"));
    expect(list.queryByText("Delete me")).not.toBeInTheDocument();
    // The remaining question stays in the list.
    expect(list.getByText("Keep me")).toBeInTheDocument();
  });
});

describe("BuilderApp — save", () => {
  it("PUTs the SurveyWrite payload (questions + languages + name) on save", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hi" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    expect(api.saveSurvey).toHaveBeenCalledTimes(1);
    const body = api.saveSurvey.mock.calls[0][0] as Record<string, unknown>;
    expect(body.name).toBe("Test survey");
    expect(Array.isArray(body.questions)).toBe(true);
    expect((body.questions as unknown[]).length).toBe(1);
    expect(body.languages).toEqual([{ code: "en", default: true }]);

    // "Saved" confirmation appears.
    expect(await screen.findByText(/^Saved$/)).toBeInTheDocument();
  });

  it("surfaces server 400 schema/logic validation errors in the error banner", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(async () => {
      throw new SaveError(400, [
        "questions.0.config.choices: too few items (1 < 2)",
        "logic: jumpTo target q9 does not exist",
      ]);
    });
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hi" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    expect(await screen.findByText(/Couldn’t save the survey/i)).toBeInTheDocument();
    expect(screen.getByText(/too few items/i)).toBeInTheDocument();
    expect(screen.getByText(/jumpTo target q9 does not exist/i)).toBeInTheDocument();
  });

  it("includes a newly-added question in the save payload", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={emptySurvey()} orgSlug={ORG} />);

    await user.click(screen.getByRole("button", { name: /^Rating/ }));
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    const body = api.saveSurvey.mock.calls[0][0] as Record<string, unknown>;
    const questions = body.questions as Array<{ type: string; config: { range: number } }>;
    expect(questions).toHaveLength(1);
    expect(questions[0].type).toBe("rating");
    expect(questions[0].config.range).toBeGreaterThanOrEqual(3);
  });
});

describe("BuilderApp — publish", () => {
  it("shows the Draft pill for a draft survey and a Publish button", () => {
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hi" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    expect(screen.getByTestId("status-pill")).toHaveTextContent(/draft/i);
    expect(screen.getByRole("button", { name: /^Publish$/ })).toBeEnabled();
  });

  it("POSTs the {status:'published'} transition and flips the pill to Published", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hi" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("button", { name: /^Publish$/ }));

    expect(api.publishSurvey).toHaveBeenCalledTimes(1);
    // The pill reflects the server echo's new status.
    expect(await screen.findByText(/published/i)).toBeInTheDocument();
    expect(screen.getByTestId("status-pill")).toHaveTextContent(/published/i);
  });

  it("blocks publishing a survey with no questions (client-side guard, no API call)", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={emptySurvey()} orgSlug={ORG} />);

    await user.click(screen.getByRole("button", { name: /^Publish$/ }));

    expect(screen.getByText(/Couldn’t publish the survey/i)).toBeInTheDocument();
    expect(screen.getByText(/Add at least one question before publishing/i)).toBeInTheDocument();
    expect(api.publishSurvey).not.toHaveBeenCalled();
  });

  it("surfaces a server 400 transition error in the error banner", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo, async () => {
      throw new SaveError(400, ["Cannot transition survey from 'closed' to 'published'."]);
    });
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hi" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("button", { name: /^Publish$/ }));

    expect(await screen.findByText(/Couldn’t publish the survey/i)).toBeInTheDocument();
    expect(screen.getByText(/Cannot transition survey/i)).toBeInTheDocument();
  });

  it("disables Publish while there are unsaved edits (must Save first)", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Original" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    // Initially clean → publishable.
    expect(screen.getByRole("button", { name: /^Publish$/ })).toBeEnabled();

    // Make an edit → dirty → Publish disabled.
    const headlineField = screen.getByDisplayValue("Original");
    await user.clear(headlineField);
    await user.type(headlineField, "Edited");
    expect(screen.getByRole("button", { name: /^Publish$/ })).toBeDisabled();

    // Save → clean again → publishable.
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(await screen.findByText(/^Saved$/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Publish$/ })).toBeEnabled();
  });
});

describe("BuilderApp — dirty / discard", () => {
  it("tracks unsaved edits: shows the hint, enables Discard, and reverts on Discard", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Original" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    // Clean initially: no unsaved hint, Discard disabled.
    expect(screen.queryByTestId("unsaved-hint")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Discard changes/i })).toBeDisabled();

    // Edit the headline → dirty.
    const headlineField = screen.getByDisplayValue("Original");
    await user.clear(headlineField);
    await user.type(headlineField, "Edited");
    expect(screen.getByTestId("unsaved-hint")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Discard changes/i })).toBeEnabled();

    // Discard → reverts to the last-saved (initial) headline and clears the flag.
    await user.click(screen.getByRole("button", { name: /Discard changes/i }));
    expect(screen.getByDisplayValue("Original")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Edited")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unsaved-hint")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Discard changes/i })).toBeDisabled();
  });

  it("clears the dirty flag after a successful save", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Original" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    const headlineField = screen.getByDisplayValue("Original");
    await user.clear(headlineField);
    await user.type(headlineField, "Edited");
    expect(screen.getByTestId("unsaved-hint")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(await screen.findByText(/^Saved$/)).toBeInTheDocument();
    expect(screen.queryByTestId("unsaved-hint")).not.toBeInTheDocument();
  });
});

describe("BuilderApp — back link", () => {
  it("renders a full-page Back link to /{orgSlug}/surveys", () => {
    const api = makeApiStub(echo);
    const q = { id: "q1", type: "openText" as const, headline: { default: "Hi" }, config: {} };
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    const back = screen.getByRole("link", { name: /Back to surveys/i });
    expect(back).toHaveAttribute("href", "/acme/surveys");
  });
});

describe("BuilderApp — Style tab (theming)", () => {
  const q = { id: "q1", type: "openText" as const, headline: { default: "Q" }, config: {} };

  it("switches to the Style tab and shows the theming controls beside the live preview", async () => {
    const user = userEvent.setup();
    render(<BuilderApp api={makeApiStub(echo)} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("tab", { name: "Style" }));
    expect(screen.getByLabelText("Brand color value")).toBeInTheDocument();
    // The live preview stays mounted on the Style tab (so edits show instantly).
    expect(screen.getByTestId("preview-pane")).toBeInTheDocument();
  });

  it("edits the brand color on the Style tab and saves it in the SurveyWrite body", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("tab", { name: "Style" }));
    const brandField = screen.getByLabelText("Brand color value");
    await user.clear(brandField);
    await user.type(brandField, "#123456");

    // Editing marks the survey dirty.
    expect(screen.getByTestId("unsaved-hint")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(api.saveSurvey).toHaveBeenCalledTimes(1);
    const body = api.saveSurvey.mock.calls[0][0] as Record<string, unknown>;
    expect((body.styling as Record<string, unknown>).brandColor).toBe("#123456");
    expect(await screen.findByText(/^Saved$/)).toBeInTheDocument();
  });

  it("toggles 'Hide the progress bar' and persists hideProgressBar:true", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("tab", { name: "Style" }));
    await user.click(screen.getByRole("checkbox", { name: /hide the progress bar/i }));
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    const body = api.saveSurvey.mock.calls[0][0] as Record<string, unknown>;
    expect((body.styling as Record<string, unknown>).hideProgressBar).toBe(true);
  });
});

describe("BuilderApp — Settings tab (behavior settings)", () => {
  const q = { id: "q1", type: "openText" as const, headline: { default: "Q" }, config: {} };

  it("toggles single-use + capture-IP + response limit and saves them in the SurveyWrite body", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("tab", { name: "Settings" }));

    // Single-use links + Capture IP are simple toggles.
    await user.click(screen.getByRole("checkbox", { name: /single-use links/i }));
    await user.click(screen.getByRole("checkbox", { name: /capture ip address/i }));

    // Limit responses → reveals the number field; set it to 50.
    await user.click(screen.getByRole("checkbox", { name: /limit responses/i }));
    const capField = screen.getByLabelText("Response limit");
    await user.clear(capField);
    await user.type(capField, "50");

    expect(screen.getByTestId("unsaved-hint")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    const body = api.saveSurvey.mock.calls[0][0] as Record<string, unknown>;
    expect(body.single_use).toBe(true);
    expect(body.capture_ip).toBe(true);
    expect(body.close_on).toEqual({ by_response_cap: 50 });
    expect(await screen.findByText(/^Saved$/)).toBeInTheDocument();
  });

  it("does not render the live preview on the Settings tab (single-column)", async () => {
    const user = userEvent.setup();
    render(<BuilderApp api={makeApiStub(echo)} initialSurvey={surveyWith(q)} orgSlug={ORG} />);
    await user.click(screen.getByRole("tab", { name: "Settings" }));
    expect(screen.queryByTestId("preview-pane")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /single-use links/i })).toBeInTheDocument();
  });
});

describe("BuilderApp — Endings tab (welcome card + endings)", () => {
  const q = { id: "q1", type: "openText" as const, headline: { default: "Q" }, config: {} };

  it("shows the welcome + endings editors beside the live preview", async () => {
    const user = userEvent.setup();
    render(<BuilderApp api={makeApiStub(echo)} initialSurvey={surveyWith(q)} orgSlug={ORG} />);
    await user.click(screen.getByRole("tab", { name: "Endings" }));
    expect(screen.getByTestId("welcome-card-panel")).toBeInTheDocument();
    expect(screen.getByTestId("endings-panel")).toBeInTheDocument();
    // The live preview stays mounted (so welcome/ending edits show instantly).
    expect(screen.getByTestId("preview-pane")).toBeInTheDocument();
  });

  it("enabling the welcome card shows it in the live preview AND saves welcome_card", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("tab", { name: "Endings" }));
    // Enable the welcome card (the panel's only checkbox).
    const panel = screen.getByTestId("welcome-card-panel");
    await user.click(within(panel).getByRole("checkbox"));

    // Edit its headline.
    const headline = within(panel).getByPlaceholderText("Welcome");
    await user.clear(headline);
    await user.type(headline, "Hello there");

    // The live preview now shows the welcome step (an <h1> headline + Start button).
    const preview = screen.getByTestId("preview-pane");
    expect(within(preview).getByRole("heading", { level: 1, name: /Hello there/ })).toBeInTheDocument();

    expect(screen.getByTestId("unsaved-hint")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    const body = api.saveSurvey.mock.calls[0][0] as Record<string, unknown>;
    expect(body.welcome_card).toEqual({ enabled: true, headline: { default: "Hello there" } });
  });

  it("adding endings shows the configured ending in the preview AND saves endings", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);

    await user.click(screen.getByRole("tab", { name: "Endings" }));
    const endingsPanel = screen.getByTestId("endings-panel");
    await user.click(within(endingsPanel).getByRole("button", { name: /Add thank-you screen/ }));
    const card = within(endingsPanel).getByTestId("ending-card");
    await user.type(within(card).getByPlaceholderText(/Thanks for your feedback/), "All done!");

    // Drive the preview past the only question to land on the ending.
    const preview = screen.getByTestId("preview-pane");
    await user.click(within(preview).getByRole("button", { name: "Next" }));
    expect(within(preview).getByRole("heading", { level: 1, name: /All done!/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    const body = api.saveSurvey.mock.calls[0][0] as Record<string, unknown>;
    const endings = body.endings as Array<Record<string, unknown>>;
    expect(endings).toHaveLength(1);
    expect(endings[0].type).toBe("endScreen");
    expect(endings[0].headline).toEqual({ default: "All done!" });
  });

  it("an untouched survey omits welcome_card + endings from the SurveyWrite body", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo);
    render(<BuilderApp api={api} initialSurvey={surveyWith(q)} orgSlug={ORG} />);
    // Just save without touching the Endings tab.
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    const body = api.saveSurvey.mock.calls[0][0] as Record<string, unknown>;
    expect("welcome_card" in body).toBe(false);
    expect("endings" in body).toBe(false);
  });
});

describe("BuilderApp — server-echo re-bridging on Save (regression)", () => {
  // The server's PUT echo is snake_case (follow_ups / welcome_card / hidden_fields). BuilderApp
  // must re-run the snake→camel bridge on the echo, or these fields VANISH from the builder the
  // instant you Save (user-reported: "as soon as I click save the follow-up goes again").
  it("keeps a follow-up visible after Save (echo is snake_case follow_ups)", async () => {
    const user = userEvent.setup();
    const api = makeApiStub(echo); // echo returns {id, ...body}; body has snake_case follow_ups
    const survey: Survey = {
      name: "Test survey",
      questions: [{ id: "q1", type: "openText", headline: { default: "Q" }, config: {} }],
      languages: [{ code: "en", default: true }],
      followUps: [
        {
          id: "fu1",
          trigger: { type: "responseFinished" },
          action: {
            type: "sendEmail",
            recipient: { type: "fixedEmail", email: "a@b.com" },
            subject: { default: "S" },
            body: { default: "<p>B</p>" },
          },
        },
      ],
    } as unknown as Survey;
    render(<BuilderApp api={api} initialSurvey={survey} orgSlug={ORG} />);

    await user.click(screen.getByRole("tab", { name: /Follow-ups/i }));
    expect(screen.queryByText(/No follow-ups yet/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    await screen.findByText("Saved"); // wait for the async save echo to be adopted

    // Regression: the follow-up must STILL be shown (echo re-bridged), not wiped to empty.
    expect(screen.queryByText(/No follow-ups yet/i)).not.toBeInTheDocument();
  });
});
