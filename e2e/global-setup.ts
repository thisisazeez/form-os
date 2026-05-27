// ============================================================================
// e2e/global-setup.ts — runs ONCE before the whole Playwright suite.
//
// Responsibilities (all idempotent):
//   1. Ensure the dedicated `parity_e2e` DB has its shared schema migrated.
//   2. Seed it (org `globex` + verified owner + workspace/env + one published
//      survey with two question types) via e2e/seed.py through manage.py shell.
//   3. Ensure the React islands are built (manifest present) so the builder/runtime
//      host templates actually mount the islands (vite_built=True).
//   4. AUTH WITHOUT PASSWORDS: mint a Django session for the seeded owner
//      server-side (no login form, no password) and persist it as Playwright
//      storageState — so every spec starts already logged-in.
//
// Everything talks to the DEDICATED instance only (DATABASE_URL=parity_e2e); it
// never touches the user's live :8011 / opensurvey_demo data.
// ============================================================================
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

import type { FullConfig } from "@playwright/test";

import {
  COOKIE_DOMAIN,
  DATABASE_URL,
  MANAGE_PY,
  PORT,
  PYTHON,
  REPO_ROOT,
  SEED_META,
  SESSION_COOKIE_NAME,
  STORAGE_STATE,
  VITE_MANIFEST,
  djangoEnv,
} from "./config";

/** Run `manage.py <args...>` against the dedicated DB; return stdout. */
function manage(args: string[]): string {
  return execFileSync(PYTHON, [MANAGE_PY, ...args], {
    cwd: REPO_ROOT,
    env: djangoEnv(),
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "inherit"],
    maxBuffer: 64 * 1024 * 1024,
  });
}

/** Run a Python snippet via `manage.py shell -c` and return stdout. */
function shell(code: string): string {
  return manage(["shell", "-c", code]);
}

/** Pull the last `MARKER=...json...` line out of mixed stdout (migrations etc. precede it). */
function scrapeMarker(out: string, marker: string): Record<string, unknown> {
  const line = out
    .split("\n")
    .reverse()
    .find((l) => l.startsWith(`${marker}=`));
  if (!line) {
    throw new Error(`Could not find ${marker}= in output:\n${out}`);
  }
  return JSON.parse(line.slice(marker.length + 1));
}

async function globalSetup(_config: FullConfig): Promise<void> {
  mkdirSync(dirname(STORAGE_STATE), { recursive: true });

  console.log(`[e2e] DB=${DATABASE_URL} port=${PORT}`);

  // 1) Shared-schema migrations (idempotent; tenant schemas migrate on org create).
  console.log("[e2e] migrate_schemas --shared …");
  manage(["migrate_schemas", "--shared", "--verbosity", "0"]);

  // 2) Seed (idempotent rebuild). seed.run() prints `E2E_SEED_RESULT={...}`.
  console.log("[e2e] seeding (org=globex, 1 published survey) …");
  const seedOut = shell("import e2e.seed as s; s.run()");
  const seed = scrapeMarker(seedOut, "E2E_SEED_RESULT");
  writeFileSync(SEED_META, JSON.stringify(seed, null, 2));
  console.log(`[e2e] seeded survey ${seed.survey_id} in org ${seed.org_slug}`);

  // 3) Build islands if the manifest is missing (the host templates gate on it).
  if (!existsSync(VITE_MANIFEST)) {
    console.log("[e2e] vite manifest missing — building frontend islands …");
    execFileSync("npm", ["run", "build"], {
      cwd: `${REPO_ROOT}/frontend`,
      stdio: "inherit",
    });
  } else {
    console.log("[e2e] vite manifest present — skipping frontend build.");
  }

  // 4) Mint a Django session for the seeded owner WITHOUT a password. Sessions live in the
  //    public schema (SHARED_APPS), so we mint with the public schema active. The three keys
  //    below are exactly what Django's ModelBackend validates on each request — omit the
  //    auth-hash and the session silently logs out.
  console.log("[e2e] minting owner session (no password) …");
  const mintOut = shell(
    [
      "from django.contrib.auth import get_user_model",
      "from django.contrib.sessions.backends.db import SessionStore",
      "from django.db import connection",
      "import json",
      "connection.set_schema_to_public()",
      "U = get_user_model()",
      `u = U.objects.get(email=${JSON.stringify(seed.owner_email)})`,
      "s = SessionStore()",
      "s['_auth_user_id'] = str(u.pk)",
      "s['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'",
      "s['_auth_user_hash'] = u.get_session_auth_hash()",
      "s.save()",
      "print('E2E_SESSION=' + json.dumps({'key': s.session_key, 'expiry': int(s.get_expiry_date().timestamp())}))",
    ].join("\n"),
  );
  const session = scrapeMarker(mintOut, "E2E_SESSION") as {
    key: string;
    expiry: number;
  };

  // 5) Persist storageState with the sessionid cookie so specs start logged-in.
  const storageState = {
    cookies: [
      {
        name: SESSION_COOKIE_NAME,
        value: session.key,
        domain: COOKIE_DOMAIN,
        path: "/",
        expires: session.expiry,
        httpOnly: true,
        secure: false,
        sameSite: "Lax" as const,
      },
    ],
    origins: [],
  };
  writeFileSync(STORAGE_STATE, JSON.stringify(storageState, null, 2));
  console.log(`[e2e] wrote storageState → ${STORAGE_STATE}`);
}

export default globalSetup;
