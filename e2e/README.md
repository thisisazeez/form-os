# OpenSurvey — Playwright E2E harness

End-to-end browser tests for the OpenSurvey Django app, driven by [Playwright](https://playwright.dev).
This frees the team from manual Chrome clicking: specs boot a dedicated app instance, seed it,
log in without a password, and exercise real user flows (dashboard, survey builder, public runtime).

## Isolation (read this first)

The harness runs against a **dedicated test instance** — it never touches the live dev server
on `:8011` / the `opensurvey_demo` database:

| Concern        | Live dev server        | This harness                 |
| -------------- | ---------------------- | ---------------------------- |
| Port           | `8011`                 | **`8099`**                   |
| Postgres DB    | `opensurvey_demo`      | **`parity_e2e`**             |
| Django settings| `config.settings.dev`  | `config.settings.dev`        |

Playwright's `webServer` boots `manage.py runserver 8099` with
`DATABASE_URL=postgres://opensurvey:opensurvey@localhost:5432/parity_e2e` and tears it down
when the run finishes. All values live in `e2e/config.ts` (override via env vars if needed).

## How to run

```bash
cd e2e
npm install                      # once — installs @playwright/test
npx playwright install chromium  # once — downloads the browser
npm run e2e                      # run the suite (headless)
npm run e2e:headed               # watch it in a real browser
npm run e2e:ui                   # Playwright's interactive UI mode
npm run e2e:report               # open the last HTML report
```

Prerequisites:

- **Postgres + Redis running** (`docker compose up -d` from the repo root). The `parity_e2e`
  database must exist — create it once: `createdb -h localhost -U opensurvey parity_e2e`
  (or `psql ... -c "CREATE DATABASE parity_e2e"`). `global-setup` runs the migrations + seed.
- **The repo's Python venv** with deps installed (default `…/opensurvey/.venv`). Override the
  interpreter with `E2E_PYTHON=/path/to/python`.
- **The React islands built** in this worktree (`frontend/dist/.vite/manifest.json`). `global-setup`
  builds them automatically if the manifest is missing; otherwise build once with
  `cd frontend && npm install && npm run build`.

## How it works

### Dedicated DB + port (`e2e/config.ts`, `playwright.config.ts`)
`playwright.config.ts` declares a `webServer` that launches the Django dev server on `8099`
(with `--noreload`) using the `parity_e2e` DB. `cwd` is the repo root so `manage.py` resolves
this worktree's code and `frontend/dist` manifest.

### Seed (`e2e/seed.py`)
A reusable, idempotent seed run through `manage.py shell`. It builds, via the production service
layer (`create_organization`):

- a verified **owner** user (`e2e-owner@globex.test`),
- a tenant **org** `globex` (auto-creates its Postgres schema, owner membership, a default
  workspace, and prod/dev environments),
- one **published survey** ("Customer Satisfaction Survey") with two question types
  (openText + rating) and a distinct ending screen.

`seed.run()` prints a machine-readable `E2E_SEED_RESULT={…}` line; `global-setup` scrapes it and
writes the ids to `e2e/.auth/seed.json`. Specs read them via `e2e/pages/seed.ts` — nothing is
hardcoded, so reseeding (or swapping the org slug) does not require touching specs.

### Password-less auth (`e2e/global-setup.ts`)
There are **no passwords anywhere**. `global-setup` mints a Django session server-side by writing
the three keys Django's `ModelBackend` validates on every request
(`_auth_user_id`, `_auth_user_backend`, `_auth_user_hash`) into a `SessionStore` (sessions live in
the public schema), then persists the resulting `sessionid` cookie as Playwright `storageState`
(`e2e/.auth/owner.json`). Every spec starts already logged-in as the org owner. The public-runtime
spec opts out of the session (`test.use({ storageState: { cookies: [], origins: [] } })`).

### Hermetic network (`e2e/fixtures.ts`)
The Django base template loads Tailwind/HTMX/Alpine/fonts from CDNs via blocking `<head>` tags.
Headless those hosts may be unreachable and a blocking head script that never loads prevents
`DOMContentLoaded` from firing → `page.goto` hangs. The shared `test` fixture **aborts all
cross-origin requests**, so specs depend only on our same-origin HTML + the locally-bundled
`/static/` islands. Import `{ test, expect }` from `../fixtures` (not `@playwright/test`).

### Page objects (`e2e/pages/`)
- `DashboardPage` — `/<org>/` welcome header + the four KPI stat cards.
- `SurveysListPage` — `/<org>/surveys` list + navigation into the builder.
- `BuilderPage` — the React builder island; tab switcher (Build / Style / Settings / Logic) with
  a per-tab hallmark control (`TAB_HALLMARK`).
- `RuntimePage` — the public survey-taker island; answer a question, click Next, reach the ending.
- `seed.ts` — typed accessor for the seed metadata.

## The 3 starter specs

1. `specs/01-dashboard.spec.ts` — the seeded `globex` dashboard renders the welcome header and all
   four stat cards.
2. `specs/02-builder-tabs.spec.ts` — open the seeded survey in the builder and switch
   Build → Style → Settings → Logic, asserting each tab's hallmark control renders.
3. `specs/03-public-runtime.spec.ts` — load the published survey's public runtime, answer the first
   question, advance, and reach the configured ending screen.

## Adding a spec

1. Create `specs/NN-name.spec.ts`.
2. `import { test, expect } from "../fixtures";` (gives you the hermetic-network page).
3. `import { seed } from "../pages/seed";` for the org slug / survey id / etc.
4. Use an existing page object or add one in `pages/`. Navigate with
   `{ waitUntil: "domcontentloaded" }` (the page objects already do this) and assert on
   roles/text (`getByRole`, `getByText`) rather than CSS classes — CDN styles are blocked, so
   class-based/visual assertions are unreliable; structure + text are stable.
5. Specs run serially against one shared seeded DB (`workers: 1`). If a spec mutates seed data,
   prefer creating its own survey/response rather than depending on another spec's side effects.

## Files not committed

`e2e/node_modules/`, `e2e/.auth/` (minted session + seed metadata), `e2e/test-results/`,
`e2e/playwright-report/` — all gitignored. `frontend/dist/` (the built islands) and
`frontend/node_modules/` are build artifacts, not committed by this harness.
