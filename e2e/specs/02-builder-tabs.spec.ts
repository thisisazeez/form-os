// ============================================================================
// Spec 2 — open the seeded survey in the builder and switch tabs
// Build → Style → Settings → Logic, asserting each tab's hallmark control renders.
//
// Starts logged-in (storageState). The builder is a React island; we wait for it to
// hydrate, then drive the segmented tab switcher and assert per-tab hallmarks
// (see e2e/pages/BuilderPage.ts TAB_HALLMARK).
// ============================================================================
import { expect, test } from "../fixtures";

import { BuilderPage, type BuilderTab } from "../pages/BuilderPage";
import { seed } from "../pages/seed";

test.describe("Survey builder", () => {
  test("switches Build → Style → Settings → Logic, each tab's control renders", async ({
    page,
  }) => {
    const { org_slug, survey_id } = seed();
    const builder = new BuilderPage(page, org_slug);

    await builder.goto(survey_id);

    // The default tab is Build — assert its hallmark up front.
    await expect(builder.tab("Build")).toHaveAttribute("aria-selected", "true");
    await expect(builder.hallmark("Build")).toBeVisible();

    // Walk every tab in order and assert its hallmark control.
    const order: BuilderTab[] = ["Build", "Style", "Settings", "Logic"];
    for (const tab of order) {
      await builder.switchTo(tab);
      await expect(
        builder.hallmark(tab),
        `Tab "${tab}" should render its hallmark control`,
      ).toBeVisible();
    }
  });
});
