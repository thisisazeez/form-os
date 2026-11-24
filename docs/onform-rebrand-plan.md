# onForm rebrand & performance plan

Status: **Phase 1–3 quick pass complete; full pytest green; Phase 4+ pending**.
Phase 3 note: the logo/favicon/OG assets and all three fonts (including the icon font) are now
self-hosted; inline-SVG icon replacement and regeneration of the 31 Stitch mocks / 9 screenshots
are deferred to a polish pass after the presentation. Phase 4 remains untouched.

Demo-day additions: the only published plan is self-hosted free (₦0/mo in NGN; no hosted
tiers or payment gateway in v1); `/` serves the marketing landing (old dispatcher moved to
`/app`); Vercel + Render deploy scripts/configs live in `deploy/` with a guide in
`deploy/README.md`.
Source repo: OpenSurvey (MIT). Preserve upstream attribution in `LICENSE` / `README.md`.

## 0. Decisions locked

| Decision | Choice |
|---|---|
| Product name | **onForm** (lowercase `on`, capital `Form`) |
| Wordmark | `on<span class="text-brand">Form</span>` |
| Tagline | **“Forms you own. No seat limits, no paywalled logic.”** |
| Positioning | Self-hosted forms, surveys & feedback — a Typeform / Formbricks alternative you fully own |
| Palette | New **indigo** system (below); replaces teal `#0d9488` |
| Domain vocabulary | Keep `Survey` model / `/surveys/` routes / JSON schema; use “form” in marketing copy only |
| Compatibility | Renames that break embeds/webhooks/keys are deferred to Phase 4; protected literals listed in §5 |
| Links | `onform/onform` is a **placeholder** for the repository URL — replace with the real fork URL in Phase 5 |

### Indigo palette (single source: `brand/tokens.json`)

| Token | Hex | Use |
|---|---|---|
| `brand` | `#5B4BE1` | Primary actions, wordmark accent, active nav |
| `brand-hover` | `#4838C4` | Hover/active state |
| `secondary` | `#0E7490` | Teal accent — a deliberate nod to the teal heritage |
| `success` | `#16A34A` | Positive states |
| `ink` | `#14161F` | Primary text |
| `muted` | `#667085` | Secondary text |
| `surface` | `#FAFAFC` | Page background |
| `panel` | `#F1F2F7` | Sidebar / card backgrounds |
| `hairline` | `rgba(20,22,31,0.10)` | Borders |

Typography: **Plus Jakarta Sans** (display) + **Inter** (body), self-hosted in Phase 3.
Icons: replace the Material Symbols font with a small inline SVG set in Phase 3.

## 1. Current state (evidence)

- ~480 matching lines across ~215 files for `OpenSurvey` / `opensurvey` / `openSurvey`.
- No central brand config: name and palette are hard-coded in templates, Django, TS/React, docs, CI, e2e, and 40+ image assets.
- Palette mirrored in three places and already inconsistent:
  `templates/base.html:21–24`, `frontend/src/lib/styling.ts:27–34`, `frontend/src/widget/styles.ts:16–26`.
- Frontend is already lean: Django-rendered shell, two React islands (`vite.config.js:34–37`), three runtime deps, no hydration.

## 2. Phase 1 — brand kernel

1. Create `brand/tokens.json` — the only authored source for name, wordmark, tagline, palette, fonts, links, attribution.
2. `config/settings/base.py` loads it into `BRAND`; add `apps/common/templatetags/brand.py` with
   `{% brand_name %}`, `{% brand_tagline %}`, `{% brand_token "brand" %}`, `{% brand_font "display" %}`,
   and `{% brand_wordmark %}`. A template tag (rather than a context processor) also works for
   `render_to_string()` email rendering, which has no request context.
3. Add `templates/brand/wordmark.html`; swap the wordmarks in `templates/base.html` and
   `templates/_layouts/app_shell.html` to the tag; inject palette/fonts into the inline Tailwind
   config via `{% brand_token %}` / `{% brand_font %}`.
4. Generate `frontend/src/lib/brand.ts` from tokens via `scripts/generate_brand.py`; wire an npm
   `brand:generate` script. `frontend/src/lib/styling.ts` and `frontend/src/widget/styles.ts` consume it.
5. Update `tests/test_app_shell.py` to assert brand tokens from settings rather than stale hex literals.

Exit gate: palette exists in one authored place; token generator is reproducible; Django template
tests pass.

## 3. Phase 2 — mechanical pass

Three explicit replacement classes (never one blind regex):

1. `OpenSurvey` → `onForm` (display strings, comments, titles, docs)
2. `opensurvey` → `onform` (DB/user names, URLs, npm names, CI, env)
3. split wordmark `Open<span class="text-brand">Survey</span>` → `on<span class="text-brand">Form</span>`

Apply across `.py`, `.ts`, `.tsx`, `.js`, `.html`, `.json`, `.md`, `.yml`, `.yaml`, `.sh`, `.txt`, `.toml`.
Protected literals (Phase 4) are placeholder-shielded before replacement and restored afterwards — see §5.
Manual follow-ups: README attribution + GitHub URLs, test assertions that inspect markup fragments,
and any generated lockfiles.

Exit gate: brand lint clean outside the allowlist; `pytest` + `vitest` + `tsc --noEmit` green.

## 4. Phases 3–7 (Phase 3 quick pass done; 4–7 pending)

| Phase | Work |
|---|---|
| 3. Visual identity — **quick pass done** | SVG logo/favicon/OG image (`frontend/public/brand/`), self-hosted Plus Jakarta Sans + Inter + Material Symbols (`frontend/public/fonts/`), favicon/OG meta in `base.html`, logo in the landing nav. **Deferred polish:** inline-SVG icon set, 31 Stitch mocks, 9 screenshots/GIFs. |
| 4. Contract compatibility | `window.onForm` + `window.openSurvey` alias; send new + legacy webhook headers; dual bundle URL / mount IDs; keep `osk_` and response edit-token salt unchanged; schema `$id` migration |
| 5. Docs & positioning | README rewrite with measured numbers; `docs/comparison.md` matrix; widget/e2e docs; issue templates; real repo URL; upstream attribution |
| 6. Performance | See §6 |
| 7. Release check | Playwright E2E, visual diff, brand lint, bundle + perf budgets |

## 5. Protected literals (deferred to Phase 4)

Do **not** rewrite these yet — they are public/migration-sensitive:

```
openSurvey*                       (window.openSurvey, openSurvey.userId, …)
OpenSurveyWidget, OpenSurveyPublicApi, OpenSurveyDeps
opensurvey-widget.js, opensurvey-widget
opensurvey-builder, opensurvey-runtime, opensurvey-builder-config, opensurvey-runtime-config
X-OpenSurvey-Signature, X-OpenSurvey-Event
osk_                              (API key prefix — changing breaks keys)
opensurvey.responses.edit_token   (salt — changing invalidates signed links)
"service": "opensurvey"           (healthz)
opensurvey_pgdata                 (Docker volume — changing discards data)
```

## 6. Performance plan (measure before/after; Phase 6)

Baseline first: bundle gzip sizes + Playwright CDP `ScriptDuration` / `TaskDuration` / long tasks for
builder load, 20 rapid keystrokes, runtime mount, widget boot; Vitest benches over the 14 logic fixtures.

Ranked work:

1. **P0** Replace `cdn.tailwindcss.com` in `templates/base.html` with build-time CSS (browser JIT is
   the largest one-time CPU cost on every page). Skip HTMX/Alpine on the two React host pages.
   **Done in Phase 3:** self-host Plus Jakarta Sans + Inter + Material Symbols; **still pending:**
   build-time Tailwind CSS and dropping the icon font for inline SVGs.
2. **P1** Take the live preview off the keystroke path: `useDeferredValue(survey)` for `PreviewPane`,
   memoize `blockKey`, style objects, and `requiredIds`.
3. **P2** Single-pass sanitize (today `QuestionRenderer.tsx:116` + `sanitizeHtml.ts:101–103` sanitize
   twice), hoist allowlists, bounded cache; compute `validateRuleAgainstSurvey` once in `LogicView`
   instead of per `RuleCard`; `React.memo` static/leaf components; stabilize inline callbacks;
   lazy `useRef(makeSessionSeed())`; stop re-registering the `message` listener every render.
4. **P3** Precompute a server-side flow index (id→index, endings, rule buckets) consumed by the TS
   evaluator; use the runtime `json_script` config to skip the initial GET; server-render the
   completed/closed/quota terminal states.
5. **P4** Confirm React/dompurify are shared before adding `manualChunks`: the current Vite build
   already emits one shared `styling-*.js` chunk (~204.64 kB / 65.27 kB gzip) consumed by both
   islands, so the earlier “two React copies” assumption is stale. If it holds, skip manual chunks;
   `React.lazy` the Logic tab and make widget boot tiny + lazy-import its renderer instead.
6. **P5** CI perf budgets: no long task > 50 ms during typing, p95 keystroke work < 16 ms, explicit
   gzip ceilings. Do not add a state manager, scheduler, graph, or drag-drop library; consider the
   React compiler only after 1–4.

## 7. Verification checklist

- [x] `python -m pytest` (Python 3.13 / Postgres, `config.settings.ci`) — green
- [x] `cd frontend && npm run typecheck && npm test && npm run build` — green
- [x] Brand lint reports only allowlisted compatibility strings
- [x] No stale hex from the old palette outside protected/generated assets
- [x] README keeps MIT attribution to the upstream author
- [ ] Perf baseline committed before Phase 6 begins
