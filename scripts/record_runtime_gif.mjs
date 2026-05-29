// ============================================================================
// scripts/record_runtime_gif.mjs — record a respondent ANSWERING a survey on the
// public runtime (type an answer → rate → submit → thank-you), as a README GIF.
//
// This submits one real response to the target instance's DB (that's the point of
// the demo). Run it against a throwaway/demo org.
//
//   cd scripts && npm install
//   BASE_URL=http://127.0.0.1:8011 ORG_SLUG=demo \
//   SURVEY_ID=<published survey id> npm run gif:runtime
//
// Requires ffmpeg on PATH. Output: docs/screenshots/runtime-demo.gif
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
const ORG_SLUG = process.env.ORG_SLUG ?? "demo";
const SURVEY_ID = process.env.SURVEY_ID ?? "67ecadce-1d8e-4a68-b02d-6f90f7a1ed06";
const OUT = resolve(REPO_ROOT, "docs", "screenshots", "runtime-demo.gif");
const SIZE = { width: 1040, height: 720 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function clickIfVisible(locator, timeout = 1500) {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    await locator.first().click();
    return true;
  } catch {
    return false;
  }
}

async function main() {
  const tmp = mkdtempSync(resolve(tmpdir(), "ossrt-"));
  mkdirSync(dirname(OUT), { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: SIZE,
    deviceScaleFactor: 1,
    recordVideo: { dir: tmp, size: SIZE },
  });
  const page = await ctx.newPage();

  // domcontentloaded (NOT the default "load") — the runtime island keeps a live
  // connection that delays the load event by ~15-30s.
  await page.goto(`${BASE_URL}/${ORG_SLUG}/s/${SURVEY_ID}`, {
    waitUntil: "domcontentloaded",
    timeout: 20000,
  });
  // Hydration is done once the first interactive control (Start or Next) is visible —
  // robust regardless of the island's container id.
  await page.getByRole("button").first().waitFor({ state: "visible", timeout: 15000 }).catch(() => {});
  await sleep(1000);

  // Some surveys open on a welcome card → click Start if present (short wait).
  await clickIfVisible(page.getByRole("button", { name: /^(Start|Begin)/i }), 500);

  // Q1: open text — type an answer, then advance.
  const textbox = page.getByRole("textbox").first();
  if (await textbox.isVisible().catch(() => false)) {
    await textbox.click();
    await textbox.pressSequentially("Jordan Avery", { delay: 55 });
    await sleep(700);
    await clickIfVisible(page.getByRole("button", { name: /Next|Submit|Finish/i }), 1500);
    await sleep(650);
  }

  // Q2: rating — pick the last star/number, then submit.
  const radios = page.getByRole("radio");
  if ((await radios.count().catch(() => 0)) > 0) {
    await radios.last().click();
    await sleep(700);
  }
  await clickIfVisible(page.getByRole("button", { name: /Next|Submit|Finish/i }), 1500);

  // Hold on the thank-you / ending screen.
  await sleep(2200);

  await ctx.close();
  await browser.close();

  const webm = readdirSync(tmp).find((f) => f.endsWith(".webm"));
  if (!webm) throw new Error("No video recorded.");
  console.log("Converting to GIF…");
  execFileSync(
    "ffmpeg",
    [
      "-y",
      "-ss", "1.0",
      "-t", "14", // safety cap — the demo flow is ~9s; never emit a runaway GIF
      "-i", resolve(tmp, webm),
      "-vf", "fps=12,scale=900:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3",
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
