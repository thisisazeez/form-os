// ============================================================================
// scripts/capture_screenshots.mjs — capture README screenshots of a RUNNING
// OpenSurvey instance, using Playwright (Chromium, retina @2x).
//
// This drives a live server READ-ONLY (it logs in and navigates; it never
// mutates data). Point it at any instance via env vars; defaults match the
// local demo org seeded by `manage.py create_demo_tenant`.
//
//   cd scripts
//   npm install
//   BASE_URL=http://127.0.0.1:8000 \
//   EMAIL=owner@example.com PASSWORD=... ORG_SLUG=demo \
//   SURVEY_ID=<a published survey id> \
//   npm run capture
//
// Output: docs/screenshots/*.png (1440x900 @2x).
// ============================================================================
import { chromium } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { mkdirSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");

const BASE_URL = process.env.BASE_URL ?? "http://127.0.0.1:8011";
const EMAIL = process.env.EMAIL ?? "owner@example.com";
const PASSWORD = process.env.PASSWORD ?? "opensurvey2026";
const ORG_SLUG = process.env.ORG_SLUG ?? "demo";
const SURVEY_ID = process.env.SURVEY_ID ?? "67ecadce-1d8e-4a68-b02d-6f90f7a1ed06";
const OUT_DIR = process.env.OUT_DIR ?? resolve(REPO_ROOT, "docs", "screenshots");

mkdirSync(OUT_DIR, { recursive: true });

/** Wait for the page to settle: network idle, React-island hydration, then a short beat. */
async function settle(page, { island = false } = {}) {
  await page.waitForLoadState("networkidle").catch(() => {});
  if (island) {
    // Islands mount into a #opensurvey-* container; wait until it has children.
    await page
      .waitForFunction(
        () => {
          const el = document.querySelector('[id^="opensurvey-"]');
          return el && el.childElementCount > 0;
        },
        { timeout: 15000 },
      )
      .catch(() => {});
  }
  await page.waitForTimeout(1300); // animations / late layout
}

async function shot(page, name) {
  const path = resolve(OUT_DIR, `${name}.png`);
  await page.screenshot({ path, fullPage: false });
  console.log(`  ✓ ${name}.png`);
}

async function main() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();

  console.log(`Capturing ${BASE_URL} -> ${OUT_DIR}`);

  // --- Logged-OUT shots first (login page + the public respondent runtime) ---
  await page.goto(`${BASE_URL}/auth/login`);
  await settle(page);
  await shot(page, "login");

  await page.goto(`${BASE_URL}/${ORG_SLUG}/s/${SURVEY_ID}`);
  await settle(page, { island: true });
  await shot(page, "runtime");

  // --- Log in (read-only thereafter) ---
  await page.goto(`${BASE_URL}/auth/login`);
  await page.fill('input[name="email"]', EMAIL);
  await page.fill('input[name="password"]', PASSWORD);
  await Promise.all([
    page.waitForLoadState("networkidle").catch(() => {}),
    page.click('button[type="submit"]'),
  ]);
  await page.waitForTimeout(800);
  if (/\/auth\/login/.test(page.url())) {
    throw new Error(`Login failed (still on ${page.url()}). Check EMAIL/PASSWORD.`);
  }

  // --- Authenticated admin shots ---
  await page.goto(`${BASE_URL}/${ORG_SLUG}/`);
  await settle(page);
  await shot(page, "dashboard");

  await page.goto(`${BASE_URL}/${ORG_SLUG}/surveys`);
  await settle(page);
  await shot(page, "surveys");

  await page.goto(`${BASE_URL}/${ORG_SLUG}/surveys/${SURVEY_ID}/edit`);
  await settle(page, { island: true });
  await shot(page, "builder");

  await page.goto(`${BASE_URL}/${ORG_SLUG}/surveys/${SURVEY_ID}/results`);
  await settle(page);
  await shot(page, "results");

  await page.goto(`${BASE_URL}/${ORG_SLUG}/contacts`);
  await settle(page);
  await shot(page, "contacts");

  await browser.close();
  console.log("Done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
