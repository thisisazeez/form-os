// ============================================================================
// e2e/pages/BuilderPage.ts — the React survey builder island
// (/<org_slug>/surveys/<id>/edit → frontend/src/islands/builder/BuilderApp.tsx).
//
// The toolbar hosts a segmented TabSwitcher (role="tablist") with four tabs:
//   Build · Style · Settings · Logic
// Each tab swaps the body; we assert a HALLMARK control per tab (stable visible text
// from the panel components — see BuilderApp.tsx / *Panel.tsx / LogicView.tsx):
//   Build    → QuestionPalette "Add question" + SettingsPanel "Survey name"
//   Style    → StylingPanel "Brand color"
//   Settings → SurveySettingsPanel "Single-use links"
//   Logic    → LogicView "Logic rules"
//
// The island mounts on #opensurvey-builder once the Vite bundle hydrates, so callers
// should await `waitForLoaded()` before switching tabs.
// ============================================================================
import type { Page, Locator } from "@playwright/test";
import { expect } from "@playwright/test";

export type BuilderTab = "Build" | "Style" | "Settings" | "Logic";

/** The hallmark control (visible text) that proves each tab rendered. */
export const TAB_HALLMARK: Record<BuilderTab, RegExp> = {
  Build: /^Add question$/,
  Style: /^Brand color$/,
  Settings: /^Single-use links$/,
  Logic: /^Logic rules$/,
};

export class BuilderPage {
  constructor(
    private readonly page: Page,
    private readonly orgSlug: string,
  ) {}

  async goto(surveyId: string): Promise<void> {
    // domcontentloaded: external CDN scripts/fonts can stall the "load" event headless;
    // the island bundle hydrates after DCL, which waitForLoaded() then awaits.
    await this.page.goto(`/${this.orgSlug}/surveys/${surveyId}/edit`, {
      waitUntil: "domcontentloaded",
    });
    await this.waitForLoaded();
  }

  /** The island mount point — present in the host HTML even before hydration. */
  mount(): Locator {
    return this.page.locator("#opensurvey-builder");
  }

  /** Wait until the React builder has hydrated (the tablist is the first rendered chrome). */
  async waitForLoaded(): Promise<void> {
    await expect(this.mount()).toBeVisible();
    await expect(this.tab("Build")).toBeVisible();
  }

  /** A tab button by its label (role="tab"). */
  tab(name: BuilderTab): Locator {
    return this.page.getByRole("tab", { name });
  }

  /** Click a tab and wait for it to become selected. */
  async switchTo(name: BuilderTab): Promise<void> {
    await this.tab(name).click();
    await expect(this.tab(name)).toHaveAttribute("aria-selected", "true");
  }

  /** The hallmark control for a tab (asserts the tab's body rendered). */
  hallmark(name: BuilderTab): Locator {
    return this.page.getByText(TAB_HALLMARK[name], { exact: false }).first();
  }
}
