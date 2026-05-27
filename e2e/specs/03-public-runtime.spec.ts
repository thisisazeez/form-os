// ============================================================================
// Spec 3 — public runtime: load the seeded PUBLISHED survey, answer the first
// question, click Next, and assert we reach the configured ending screen.
//
// The public runtime is ANONYMOUS, so this spec clears storageState (no session).
// The seeded survey has no welcome card and a required openText first question, so the
// runtime opens straight on it. Answering + Next persists the response, finalizes it,
// and renders the ending with the seed's distinct ending headline.
// ============================================================================
import { expect, test } from "../fixtures";

import { RuntimePage } from "../pages/RuntimePage";
import { seed } from "../pages/seed";

// The public path is unauthenticated — run it with no stored session.
test.use({ storageState: { cookies: [], origins: [] } });

test.describe("Public runtime", () => {
  test("answer the first question and reach the ending screen", async ({ page }) => {
    const { org_slug, survey_id, ending_headline } = seed();
    const runtime = new RuntimePage(page, org_slug);

    await runtime.goto(survey_id);

    // First question (seeded openText) is shown.
    await expect(runtime.questionHeading(/what is your name\?/i)).toBeVisible();

    // Answer it and advance.
    await runtime.answerText("Playwright Tester");
    await runtime.clickNext();

    // We reach the SECOND question (rating) — the openText answer was accepted + persisted.
    await expect(runtime.questionHeading(/how would you rate us\?/i)).toBeVisible();

    // Rating is optional — advancing finalizes the response and reaches the ending.
    await runtime.clickNext();

    // The configured ending screen renders (distinct seed headline, not the generic default).
    await expect(runtime.endingHeading(new RegExp(escapeRegExp(ending_headline), "i"))).toBeVisible();
  });
});

/** Escape regex metacharacters so the ending headline (contains "—") matches literally. */
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
