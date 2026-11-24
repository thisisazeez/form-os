// ============================================================================
// playwright.config.ts — onForm parity E2E harness.
//
// Boots a DEDICATED Django dev server on port 8099 backed by the `parity_e2e`
// Postgres DB (NOT the user's live :8011 / onform_demo). A global-setup
// migrates + seeds the DB, builds the React islands if needed, and mints a
// password-less Django session persisted as storageState so every spec starts
// already logged-in as the seeded org owner.
// ============================================================================
import { defineConfig, devices } from "@playwright/test";

import {
  BASE_URL,
  DATABASE_URL,
  DJANGO_SETTINGS_MODULE,
  MANAGE_PY,
  PORT,
  PYTHON,
  REPO_ROOT,
  STORAGE_STATE,
} from "./config";

export default defineConfig({
  testDir: "./specs",
  globalSetup: "./global-setup.ts",
  // Generous-but-bounded timeouts; the islands hydrate client-side.
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // specs share one seeded DB/instance; keep ordering deterministic
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],

  use: {
    baseURL: BASE_URL,
    storageState: STORAGE_STATE, // logged-in by default (specs can override per-test)
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // Boot the dedicated Django instance. cwd is the repo root (absolute) so manage.py
  // resolves the worktree's code + frontend/dist manifest. The DB + settings env force
  // total isolation from the live dev server.
  webServer: {
    command: `${PYTHON} ${MANAGE_PY} runserver ${PORT} --noreload`,
    cwd: REPO_ROOT,
    port: PORT,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      DATABASE_URL,
      DJANGO_SETTINGS_MODULE,
    },
  },
});
