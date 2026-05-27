# OpenSurvey embeddable widget

A single, self-contained `<script>` a customer drops on their website/app to show
**in-product ("app") surveys**. It reads its config from its own `<script>` tag, identifies
the visitor, fires triggers, and renders matched surveys by **reusing the existing public
survey runtime** (no separate renderer). It consumes only the existing public widget APIs
(`apps/actions/public_*` + the public survey GET / response create-patch-finalize) — it adds
**no** backend surface.

---

## 1. Embed snippet (the contract)

```html
<script
  src="<APP_ORIGIN>/static/widget/opensurvey-widget.js"
  data-environment-id="<ENV_ID>"
  data-org-slug="<ORG_SLUG>">
</script>
```

### Why `data-org-slug` is REQUIRED

The public widget APIs are **org-slug-prefixed**: `/<apiBase>/<orgSlug>/public/...`.
`TenantSlugMiddleware` (`apps/common/middleware.py`, ADR-001) resolves the tenant **schema**
from that slug in the URL path; it then strips it before the public routes match. There is
**no env-only public route** — the environment UUID alone cannot select the Postgres schema.
So the widget must know the org slug. (We inspected `apps/actions/public_urls.py` +
`apps/surveys/public_urls.py` + `apps/responses/public_urls.py` and the middleware to confirm
this; an env-only route would require a backend change, which is out of scope.)

### Optional attributes

| Attribute | Default | Meaning |
|---|---|---|
| `data-api-host` | the widget script's own origin | API host origin (for a cross-origin embed, e.g. CDN-hosted script hitting the app API). |
| `data-api-base` | `/api/v1` | API path prefix in front of the org slug. |
| `data-pageview-action` | `page_view` | The action **name** auto-tracked on every page view. Must match an `ActionClass` name/key in the environment to fire anything. |
| `data-page-url-match` | _(none)_ | If set, the pageview only tracks when the current URL matches this substring or `*`-glob (a cheap **client-side pre-filter**; the server is still the authority). |
| `data-click-action` | _(none)_ | The action **name** tracked when a click matches `data-click-selector`. |
| `data-click-selector` | _(none)_ | A CSS selector; a delegated click matching it fires `data-click-action`. |
| `data-disable-pageview` | `false` | Set `"true"` to suppress automatic pageview tracking entirely. |

Same-origin embed (the common case — script served by the app itself) needs only the two
required attributes; `data-api-host` defaults to the script's origin.

---

## 2. JS API — `window.openSurvey`

The script auto-boots on load and exposes:

```js
openSurvey.init();                       // re-run boot (idempotent; auto-called on load)
openSurvey.setUserId("<your-user-id>");  // identify the host's user (persisted)
openSurvey.track("signed_up", { plan: "pro" }); // the "code" trigger
openSurvey.reset();                      // forget the user + in-session de-dup (new visitor)
openSurvey.ready;                         // boolean — true once config resolved + booted
```

- **userId**: on init the widget reuses a persisted id (localStorage key `openSurvey.userId`)
  or generates + persists a new one. `setUserId(id)` overrides + persists it (use it once you
  know the logged-in user's id). It is sent to every `track` / `display` call and the server
  maps it to a per-environment `Contact.external_id`.
- **track(action, props?)**: the explicit/code trigger. `props` are forwarded as contact
  `attributes` (best-effort traits). Never throws into the host page on a network error.
- **reset()**: clears the userId + the in-session "already shown" set and dismisses any open
  survey — a clean slate for a new visitor (e.g. on logout).

> The supported embed is a normal `<script src=...>` (the IIFE bundle), which auto-boots on
> load: it resolves the `data-*` config, sets `window.openSurvey`, and runs `init()`. The
> `data-*` tag must be present in the DOM when the bundle executes (the standard case). Loading
> the bundle in an exotic way that defers/strips the tag is not a supported configuration.

---

## 3. How triggers map (READ THIS — it explains the design)

**All three trigger types funnel through `track(name)`; the SERVER is the matching oracle.**

The app-survey **catalog** (`GET .../app-surveys`) returns each survey's `triggers` as a flat
list of **`ActionClass` id-strings** only. There is **no public endpoint** that exposes an
ActionClass's `name` / `type` / `config.urlMatch` / `config.cssSelector`. So the widget cannot
do client-side urlMatch/cssSelector matching against the server's action definitions — that
data is not reachable without a backend change (out of scope). Instead, the widget sends an
**action name** to `track`, and the server resolves it (by `config.key`, then `name`) to an
`ActionClass` and returns the surveys whose `triggers` include it.

| Trigger | What the widget does | What you configure |
|---|---|---|
| **code** | `openSurvey.track("signed_up")` → `POST .../actions/track` | nothing; call it from your app |
| **pageview** | auto-fires `track(<data-pageview-action>)` on initial load + every SPA `pushState` / `replaceState` / `popstate` | create a `pageview` ActionClass named (or keyed) `page_view`; optionally narrow with `data-page-url-match` |
| **click** | a single delegated `click` listener; on a click matching `data-click-selector`, fires `track(<data-click-action>)` | create a `click` ActionClass; set `data-click-action` + `data-click-selector` |

The catalog GET on init is **session warm-up + the in-session de-dup baseline**, not the
decision oracle. The decision (which surveys to show now) always comes from `track`.

### Fire → show pipeline

When any trigger fires:

1. `POST .../environments/<env>/actions/track` `{userId, action[, attributes, url]}` → server
   returns `{surveys: [...]}` (already gated by displayLimit / recontact cooldown /
   displayPercentage).
2. For each returned survey **not already shown this tab session**:
   1. mark it shown (sessionStorage `openSurvey.shown`) — the cheap **in-session de-dup**;
   2. `POST .../app-surveys/<survey>/displays` `{userId}` — record the impression keyed to the
      userId-resolved Contact (so the server-side displayLimit + recontact counters work);
   3. **render it** (see §4).

The server enforces cross-session gating; the widget adds the in-tab "don't re-show this
session" guard.

---

## 4. Rendering reuses the existing runtime

`renderSurvey()` (`render.tsx`) injects a host element with a **shadow root** into
`document.body`, then mounts the existing public runtime — **`SurveyRuntime` + `RuntimeApi`**
from `src/islands/runtime/` — inside it. The runtime:

- loads the full survey definition from the existing public GET
  (`GET /<apiBase>/<orgSlug>/public/surveys/<id>`),
- runs the normal **response create → patch (save-as-you-go) → finalize** loop on submit,

…exactly as on the hosted `/<org>/s/<id>` link runtime. The widget does **not** reimplement
any question rendering or submission. A close (`×`) button dismisses the survey (unmounts the
React tree + removes the shadow host).

The shadow root gives DOM + style isolation from the host page.

---

## 5. Build & serving

A **separate** Vite target (`frontend/vite.widget.config.ts`) — kept apart from the
django-vite island pipeline (`vite.config.js`) — builds a single self-contained **IIFE** with
React bundled **in** (the host page needs nothing):

```
npm run build:widget     # builds just the widget
npm run build            # builds the islands (vite build) THEN the widget
```

Output (stable, **unhashed** name so the embed `<script src>` is a fixed URL):

```
frontend/dist/widget/opensurvey-widget.js
   → collectstatic → STATIC_ROOT/widget/opensurvey-widget.js
   → served by Django at  /static/widget/opensurvey-widget.js
```

`frontend/dist` is already in `STATICFILES_DIRS`, and the project uses the default
(non-hashing) static storage, so the file is served at that fixed path in both dev
(`runserver` + `staticfiles`) and prod (`collectstatic`). The widget build uses
`emptyOutDir: false` and writes to a disjoint `dist/widget/` subtree, so it never clobbers the
island bundles (`dist/assets/` + `dist/.vite/manifest.json`) and vice-versa.

---

## 6. Tests

`npm run test` (vitest + jsdom, mocked fetch):

- `config.test.ts` — data-* parsing, required-attr guard, apiHost defaulting/override.
- `storage.test.ts` — userId generate/persist/override/reset; in-session de-dup.
- `api.test.ts` — tenant-prefixed URL construction + request bodies for all three endpoints.
- `core.test.ts` — init warms catalog + persists userId; pageview (load + SPA pushState) /
  click / code triggers call track with the right body; returned survey renders, display
  posted once, in-session de-dup; setUserId; tracking failure never throws; reset.
- `render.test.tsx` — `renderSurvey` mounts the **reused runtime** in shadow DOM, loads the
  definition via the public survey GET, and runs create → finalize on submit.
- `main.test.ts` — `boot()` reads data-*, exposes `window.openSurvey`, is a no-op singleton,
  and disables itself (no throw) when required attributes are missing.

---

## 7. Deferred / honest limitations

- **Exact visual parity inside shadow DOM.** The runtime is styled with Tailwind utility
  classes that, on the hosted pages, resolve against the Tailwind CDN config in
  `templates/base.html`. Those don't cascade into the shadow root, so the widget injects a
  **curated subset** of those utilities + brand tokens (`styles.ts`). It is presentable and
  on-brand, but not a full Tailwind build — pixel-exact parity is deferred (a compiled
  Tailwind stylesheet scoped to the shadow root would close the gap).
- **Client-side urlMatch / cssSelector matching against server action definitions.** Not
  possible without a public actions-definitions endpoint (see §3). The widget approximates
  pageview targeting with the optional `data-page-url-match` pre-filter and click targeting
  with `data-click-selector`; precise server-defined matching is deferred.
- **React Native / mobile SDK.** Web-only; the matching engine is model-agnostic so an RN
  surface could reuse the same APIs later.
- **Survey placement / animation options** beyond the bottom-right popup card.
```
