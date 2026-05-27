// ============================================================================
// e2e/config.ts — single source of truth for the harness' environment.
//
// All ports, paths, DB URLs and the Python interpreter live here so specs,
// the global-setup, and playwright.config.ts agree. ISOLATION is the whole point:
// we run a DEDICATED Django instance on port 8099 backed by the `parity_e2e`
// Postgres DB — NEVER the user's live :8011 dev server / opensurvey_demo DB.
// ============================================================================
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Repo root (the Django project) — e2e/ lives one level below it. */
export const REPO_ROOT = resolve(__dirname, "..");

/** Dedicated E2E Postgres DB — isolated from the live opensurvey_demo. */
export const DATABASE_URL =
  process.env.E2E_DATABASE_URL ??
  "postgres://opensurvey:opensurvey@localhost:5432/parity_e2e";

/** Dedicated E2E port — NOT :8011 (the user's live dev server). */
export const PORT = Number(process.env.E2E_PORT ?? 8099);
export const BASE_URL = `http://localhost:${PORT}`;

/** Django settings module for the E2E server. */
export const DJANGO_SETTINGS_MODULE =
  process.env.E2E_DJANGO_SETTINGS_MODULE ?? "config.settings.dev";

/**
 * Python interpreter. Defaults to the canonical repo's venv (has every dep + matches
 * the worktree's identical code). Override with E2E_PYTHON for a different env.
 */
export const PYTHON =
  process.env.E2E_PYTHON ??
  "/Users/mohitbadwal/PycharmProjects/opensurvey/.venv/bin/python";

export const MANAGE_PY = resolve(REPO_ROOT, "manage.py");

/** The Vite manifest the Django host templates need to mount the React islands. */
export const VITE_MANIFEST = resolve(
  REPO_ROOT,
  "frontend",
  "dist",
  ".vite",
  "manifest.json",
);

/** Where global-setup persists the logged-in browser state + seed metadata. */
export const STORAGE_STATE = resolve(__dirname, ".auth", "owner.json");
export const SEED_META = resolve(__dirname, ".auth", "seed.json");

/** Stable cookie/identity constants (mirror e2e/seed.py + settings.SESSION_COOKIE_NAME). */
export const SESSION_COOKIE_NAME = "sessionid";
export const COOKIE_DOMAIN = "localhost";

/** The env block passed to every manage.py invocation (DB + settings isolation). */
export function djangoEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    DATABASE_URL,
    DJANGO_SETTINGS_MODULE,
  };
}
