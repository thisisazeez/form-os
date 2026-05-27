// ============================================================================
// Spec 1 — the seeded-tenant (globex) dashboard renders its four KPI stat cards.
//
// Starts logged-in (storageState minted by global-setup). Loads /<org_slug>/ and
// asserts the "Welcome back" header + all four stat-card labels render.
// ============================================================================
import { expect, test } from "../fixtures";

import { DashboardPage, STAT_LABELS } from "../pages/DashboardPage";
import { seed } from "../pages/seed";

test.describe("Dashboard", () => {
  test("renders the welcome header and all four stat cards", async ({ page }) => {
    const { org_slug } = seed();
    const dashboard = new DashboardPage(page, org_slug);

    await dashboard.goto();

    // Authenticated dashboard (not the login page).
    await expect(page).toHaveURL(new RegExp(`/${org_slug}/?$`));
    await expect(dashboard.heading()).toBeVisible();

    // All four KPI stat cards present.
    for (const label of STAT_LABELS) {
      await expect(dashboard.statCard(label)).toBeVisible();
    }

    // The recent-surveys panel links into the list.
    await expect(dashboard.viewAllSurveys()).toBeVisible();
  });
});
