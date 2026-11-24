// ============================================================================
// scripts/record_demo_gif.mjs — record an animated demo of the survey builder
// (add questions, watch the live preview update) and convert it to a GIF for the
// README. Drives a running instance READ-ONLY: it never clicks Save/Publish, so
// the builder edits stay client-side and no data is mutated.
//
//   cd scripts && npm install
//   BASE_URL=http://127.0.0.1:8011 EMAIL=owner@example.com PASSWORD=onform2026 \
//   ORG_SLUG=demo SURVEY_ID=<published survey id> npm run gif
//
// Requires ffmpeg on PATH. Output: docs/screenshots/builder-demo.gif
// ============================================================================
import { chromium } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { mkdtempSync, mkdirSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");

const BASE_URL = process.env.BASE_URL ?? "http://127.0.0.1:8011";
const EMAIL = process.env.EMAIL ?? "owner@example.com";
const PASSWORD = process.env.PASSWORD ?? "onform2026";
const ORG_SLUG = process.env.ORG_SLUG ?? "demo";
const SURVEY_ID = process.env.SURVEY_ID ?? "67ecadce-1d8e-4a68-b02d-6f90f7a1ed06";
const OUT = resolve(REPO_ROOT, "docs", "screenshots", "builder-demo.gif");
const SIZE = { width: 1320, height: 1040 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(resolve(tmpdir(), "ossgif-"));
  mkdirSync(dirname(OUT), { recursive: true });
  const browser = await chromium.launch();

  // --- 1) Log in OFF-CAMERA, persist the session. ---
  const auth = await browser.newContext({ viewport: SIZE });
  const lp = await auth.newPage();
  await lp.goto(`${BASE_URL}/auth/login`);
  await lp.fill('input[name="email"]', EMAIL);
  await lp.fill('input[name="password"]', PASSWORD);
  await Promise.all([
    lp.waitForLoadState("networkidle").catch(() => {}),
    lp.click('button[type="submit"]'),
  ]);
  await sleep(600);
  if (/\/auth\/login/.test(lp.url())) throw new Error("Login failed — check EMAIL/PASSWORD.");
  const statePath = resolve(tmp, "state.json");
  await auth.storageState({ path: statePath });
  await auth.close();

  // --- 2) Record ONLY the builder interactions. ---
  const ctx = await browser.newContext({
    viewport: SIZE,
    storageState: statePath,
    recordVideo: { dir: tmp, size: SIZE },
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE_URL}/${ORG_SLUG}/surveys/${SURVEY_ID}/edit`);
  await page.waitForLoadState("networkidle").catch(() => {});
  // Palette buttons carry a unique `title` (the hint) — robust vs. duplicate label text
  // that also appears in the question list.
  // Palette buttons carry a unique `title` (the hint) — robust vs. duplicate label text
  // that also appears in the question list.
  const byHint = (h) => page.locator(`button[title*=${JSON.stringify(h)}]`);
  const npsBtn = byHint("recommendation score");
  const ratingBtn = byHint("Stars");
  const dateBtn = byHint("Date picker");
  // wait for island hydration: the palette must be interactive
  await npsBtn.waitFor({ timeout: 15000 });
  await sleep(1600); // hold on the initial builder view (palette + live preview)

  // Click through a few question types — each is added to the survey and the question
  // list grows. (We never Save, so this is all client-side / non-mutating.)
  await npsBtn.click();
  await sleep(900);
  await ratingBtn.click();
  await sleep(900);
  await dateBtn.click();
  await sleep(1100);

  // Gently scroll down to reveal the growing question list, then back up to the preview.
  for (let y = 0; y <= 520; y += 26) {
    await page.mouse.wheel(0, 26);
    await sleep(45);
  }
  await sleep(1700);
  for (let y = 520; y >= 0; y -= 40) {
    await page.mouse.wheel(0, -40);
    await sleep(40);
  }
  await sleep(1300);

  await ctx.close(); // finalizes the .webm
  await browser.close();

  // --- 3) Convert webm -> high-quality GIF (palettegen). ---
  const webm = readdirSync(tmp).find((f) => f.endsWith(".webm"));
  if (!webm) throw new Error("No video recorded.");
  const src = resolve(tmp, webm);
  console.log("Converting to GIF…");
  execFileSync(
    "ffmpeg",
    [
      "-y",
      "-ss", "1.2", // skip the blank page-load lead-in
      "-i", src,
      "-vf", "fps=13,scale=1000:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer",
      "-loop", "0",
      OUT,
    ],
    { stdio: "inherit" },
  );
  console.log(`Done -> ${OUT}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
