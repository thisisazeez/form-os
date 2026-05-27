// ============================================================================
// e2e/pages/DashboardPage.ts — the tenant dashboard (/<org_slug>/).
//
// Renders server-side (templates/dashboard/home.html): a "Welcome back" header and
// four KPI stat cards (Active surveys / Total responses / Avg. completion / Avg. NPS),
// each a templates/_components/stat_card.html instance (label = muted <p>, value =
// large <span>).
// ============================================================================
import type { Page, Locator } from "@playwright/test";

export const STAT_LABELS = [
  "Active surveys",
  "Total responses",
  "Avg. completion",
  "Avg. NPS",
] as const;

export class DashboardPage {
  constructor(
    private readonly page: Page,
    private readonly orgSlug: string,
  ) {}

  async goto(): Promise<void> {
    // domcontentloaded (not "load"): the page pulls external CDN scripts/fonts whose
    // load event can hang headless; the server-rendered DOM is ready at DCL.
    await this.page.goto(`/${this.orgSlug}/`, { waitUntil: "domcontentloaded" });
  }

  /** The "Welcome back…" heading proves the authenticated dashboard rendered. */
  heading(): Locator {
    return this.page.getByRole("heading", { name: /welcome back/i });
  }

  /** A stat card located by its visible label text (the muted <p> in stat_card.html). */
  statCard(label: string): Locator {
    return this.page.getByText(label, { exact: true });
  }

  /** Link into the surveys list (the topbar/“View all” affordance). */
  viewAllSurveys(): Locator {
    return this.page.getByRole("link", { name: /view all/i });
  }
}
