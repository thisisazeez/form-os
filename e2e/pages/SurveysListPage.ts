// ============================================================================
// e2e/pages/SurveysListPage.ts — the surveys list (/<org_slug>/surveys).
//
// Server-rendered grid of the org's surveys (scoped to readable workspaces) with a
// "+ New survey" CTA and status filter chips. A survey row links to its results;
// the builder is reached at /<org_slug>/surveys/<id>/edit.
// ============================================================================
import type { Page, Locator } from "@playwright/test";

export class SurveysListPage {
  constructor(
    private readonly page: Page,
    private readonly orgSlug: string,
  ) {}

  async goto(): Promise<void> {
    await this.page.goto(`/${this.orgSlug}/surveys`);
  }

  /** A survey entry located by its visible name. */
  surveyByName(name: string): Locator {
    return this.page.getByText(name, { exact: true }).first();
  }

  /** Navigate directly to the builder host page for a survey id. */
  async openBuilder(surveyId: string): Promise<void> {
    await this.page.goto(`/${this.orgSlug}/surveys/${surveyId}/edit`);
  }
}
