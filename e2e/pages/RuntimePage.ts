// ============================================================================
// e2e/pages/RuntimePage.ts — the public survey runtime island
// (/<org_slug>/s/<survey_id> → frontend/src/islands/runtime/SurveyRuntime.tsx).
//
// Anonymous (no auth). The island fetches the published survey definition, then drives:
//   welcome card (if any) → question(s) → ending/thank-you.
// For the seeded survey (no welcome card, first question = required openText), the runtime
// jumps straight to the question. Answering + clicking "Next" persists + finalizes the
// response and renders the ending screen with the survey's configured ending headline.
//
// Selectors: question headline is an <h2> (QuestionRenderer shell); openText is a text
// <input> (role=textbox); the primary action button is labelled "Next".
// ============================================================================
import type { Page, Locator } from "@playwright/test";
import { expect } from "@playwright/test";

export class RuntimePage {
  constructor(
    private readonly page: Page,
    private readonly orgSlug: string,
  ) {}

  /** Open the public runtime for a published survey. linkToken is optional (seed isn't single-use). */
  async goto(surveyId: string, linkToken?: string): Promise<void> {
    const qs = linkToken ? `?link_token=${encodeURIComponent(linkToken)}` : "";
    // domcontentloaded: external CDN scripts/fonts can stall the "load" event headless;
    // the runtime island hydrates after DCL, which waitForLoaded() then awaits.
    await this.page.goto(`/${this.orgSlug}/s/${surveyId}${qs}`, {
      waitUntil: "domcontentloaded",
    });
    await this.waitForLoaded();
  }

  /** The island mount point — present in the host HTML even before hydration. */
  mount(): Locator {
    return this.page.locator("#opensurvey-runtime");
  }

  /** Wait until the runtime has hydrated and shown the first question (or welcome). */
  async waitForLoaded(): Promise<void> {
    await expect(this.mount()).toBeVisible();
    // The first heading appears once the island resolves the survey + first step.
    await expect(this.page.getByRole("heading").first()).toBeVisible();
  }

  /** The current question's headline heading (the QuestionRenderer <h2>). */
  questionHeading(text: string | RegExp): Locator {
    return this.page.getByRole("heading", { name: text });
  }

  /** The open-text answer field (role=textbox). */
  openTextInput(): Locator {
    return this.page.getByRole("textbox").first();
  }

  /** Type an answer into the first text input. */
  async answerText(value: string): Promise<void> {
    await this.openTextInput().fill(value);
  }

  /** The primary "Next" action. */
  nextButton(): Locator {
    return this.page.getByRole("button", { name: /^Next$/ });
  }

  async clickNext(): Promise<void> {
    await this.nextButton().click();
  }

  /** A heading matching the configured ending screen text (proves we reached an ending). */
  endingHeading(text: string | RegExp): Locator {
    return this.page.getByRole("heading", { name: text });
  }
}
