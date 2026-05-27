// ============================================================================
// EndingsPanel tests — the Welcome-card + Endings authoring editors.
//
// Covers the authoring surface the task adds:
//   - welcome card: toggle on (seeds enabled+headline) / off (welcomeCard:null);
//     edit headline/html/buttonLabel; multi-language headline preserves `default`.
//   - endings: add a thank-you screen + a redirect (distinct shapes); edit per-type
//     fields; reorder (up/down, ids stable); remove.
//
// The panels are CONTROLLED — they emit a fresh Survey via onChange (the surveyModel
// transforms), so the harness re-renders with the new survey to assert the next edit.
// ============================================================================
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type { Survey } from "../../lib/types";
import { EndingsPanel, WelcomeCardPanel } from "./EndingsPanel";

function baseSurvey(): Survey {
  return {
    name: "S",
    questions: [{ id: "q1", type: "openText", headline: { default: "Q1" }, config: {} }],
    languages: [{ code: "en", default: true }],
  };
}

function multiLangSurvey(): Survey {
  return {
    ...baseSurvey(),
    languages: [
      { code: "en", default: true },
      { code: "fr", default: false },
    ],
  };
}

/** A controlled harness around WelcomeCardPanel that tracks the survey + active lang. */
function WelcomeHarness({ initial, lang = "default" }: { initial: Survey; lang?: string }) {
  const [survey, setSurvey] = useState(initial);
  const defaultLang = survey.languages.find((l) => l.default)?.code ?? "default";
  return (
    <>
      <WelcomeCardPanel
        survey={survey}
        onChange={setSurvey}
        lang={lang}
        defaultLang={defaultLang}
      />
      <pre data-testid="state">{JSON.stringify(survey.welcomeCard ?? null)}</pre>
    </>
  );
}

/** A controlled harness around EndingsPanel. */
function EndingsHarness({ initial, lang = "default" }: { initial: Survey; lang?: string }) {
  const [survey, setSurvey] = useState(initial);
  const defaultLang = survey.languages.find((l) => l.default)?.code ?? "default";
  return (
    <>
      <EndingsPanel survey={survey} onChange={setSurvey} lang={lang} defaultLang={defaultLang} />
      <pre data-testid="state">{JSON.stringify(survey.endings ?? [])}</pre>
    </>
  );
}

function welcomeState() {
  return JSON.parse(screen.getByTestId("state").textContent || "null");
}
function endingsState() {
  return JSON.parse(screen.getByTestId("state").textContent || "[]");
}

describe("WelcomeCardPanel", () => {
  it("starts disabled and shows no editor fields", () => {
    render(<WelcomeHarness initial={baseSurvey()} />);
    expect(screen.getByText(/Disabled/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Welcome")).not.toBeInTheDocument();
    expect(welcomeState()).toBeNull();
  });

  it("toggling on seeds enabled + a default headline; fields appear", async () => {
    const user = userEvent.setup();
    render(<WelcomeHarness initial={baseSurvey()} />);
    await user.click(screen.getByRole("checkbox"));
    expect(welcomeState()).toEqual({ enabled: true, headline: { default: "Welcome" } });
    // Editor fields now render.
    expect(screen.getByPlaceholderText("Welcome")).toBeInTheDocument();
  });

  it("editing headline/html/button writes the default-lang keys", async () => {
    const user = userEvent.setup();
    render(<WelcomeHarness initial={baseSurvey()} />);
    await user.click(screen.getByRole("checkbox"));

    const headline = screen.getByPlaceholderText("Welcome");
    await user.clear(headline);
    await user.type(headline, "Hi");
    expect(welcomeState().headline).toEqual({ default: "Hi" });

    await user.type(screen.getByPlaceholderText(/Thanks for taking/), "<p>Body</p>");
    await user.type(screen.getByPlaceholderText("Start"), "Begin");
    const st = welcomeState();
    expect(st.html).toEqual({ default: "<p>Body</p>" });
    expect(st.buttonLabel).toEqual({ default: "Begin" });
  });

  it("toggling off writes welcomeCard:null", async () => {
    const user = userEvent.setup();
    const s = baseSurvey();
    s.welcomeCard = { enabled: true, headline: { default: "Welcome" } };
    render(<WelcomeHarness initial={s} />);
    await user.click(screen.getByRole("checkbox"));
    expect(welcomeState()).toBeNull();
  });

  it("multi-language: editing the fr headline preserves the default key", async () => {
    const user = userEvent.setup();
    const s = multiLangSurvey();
    s.welcomeCard = { enabled: true, headline: { default: "Welcome" } };
    render(<WelcomeHarness initial={s} lang="fr" />);
    const headline = screen.getByPlaceholderText("Welcome");
    await user.type(headline, "Bienvenue");
    expect(welcomeState().headline).toEqual({ default: "Welcome", fr: "Bienvenue" });
  });
});

describe("EndingsPanel", () => {
  it("starts empty with the default-thank-you hint", () => {
    render(<EndingsHarness initial={baseSurvey()} />);
    expect(screen.getByText(/No custom endings/i)).toBeInTheDocument();
    expect(endingsState()).toEqual([]);
  });

  it("adds a thank-you screen (minimal {id,type})", async () => {
    const user = userEvent.setup();
    render(<EndingsHarness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add thank-you screen/ }));
    const eds = endingsState();
    expect(eds).toHaveLength(1);
    expect(eds[0].type).toBe("endScreen");
    expect(Object.keys(eds[0]).sort()).toEqual(["id", "type"]);
  });

  it("adds a redirect (seeds the required empty redirectUrl)", async () => {
    const user = userEvent.setup();
    render(<EndingsHarness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add redirect/ }));
    const eds = endingsState();
    expect(eds[0].type).toBe("redirect");
    expect(eds[0].redirectUrl).toBe("");
    // The redirect card shows the URL field, not the thank-you headline field.
    expect(screen.getByPlaceholderText(/example.com\/thank-you/)).toBeInTheDocument();
  });

  it("edits a thank-you screen's headline / subheader", async () => {
    const user = userEvent.setup();
    render(<EndingsHarness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add thank-you screen/ }));
    await user.type(screen.getByPlaceholderText(/Thanks for your feedback/), "Done!");
    await user.type(screen.getByPlaceholderText(/helps us improve/), "Cheers");
    const e = endingsState()[0];
    expect(e.headline).toEqual({ default: "Done!" });
    expect(e.subheader).toEqual({ default: "Cheers" });
  });

  it("switching the type to redirect keeps the id and seeds redirectUrl", async () => {
    const user = userEvent.setup();
    render(<EndingsHarness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add thank-you screen/ }));
    const id = endingsState()[0].id;
    await user.selectOptions(screen.getByLabelText("Type"), "redirect");
    const e = endingsState()[0];
    expect(e.id).toBe(id); // id stable
    expect(e.type).toBe("redirect");
    expect(e.redirectUrl).toBe("");
  });

  it("reorders endings (ids stable) and removes one", async () => {
    const user = userEvent.setup();
    render(<EndingsHarness initial={baseSurvey()} />);
    await user.click(screen.getByRole("button", { name: /Add thank-you screen/ }));
    await user.click(screen.getByRole("button", { name: /Add redirect/ }));
    const [first, second] = endingsState().map((e: { id: string }) => e.id);

    // Move the 2nd ending up → order flips, ids preserved.
    await user.click(screen.getByRole("button", { name: /Move ending 2 up/ }));
    expect(endingsState().map((e: { id: string }) => e.id)).toEqual([second, first]);

    // Remove the (now-first) ending.
    await user.click(screen.getByRole("button", { name: /Remove ending 1/ }));
    expect(endingsState().map((e: { id: string }) => e.id)).toEqual([first]);
  });

  it("multi-language: editing the fr ending headline preserves the default key", async () => {
    const user = userEvent.setup();
    const s = multiLangSurvey();
    s.endings = [{ id: "e1", type: "endScreen", headline: { default: "Thanks" } }];
    render(<EndingsHarness initial={s} lang="fr" />);
    const card = screen.getByTestId("ending-card");
    await user.type(within(card).getByPlaceholderText(/Thanks for your feedback/), "Merci");
    expect(endingsState()[0].headline).toEqual({ default: "Thanks", fr: "Merci" });
  });
});
