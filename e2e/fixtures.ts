// ============================================================================
// e2e/fixtures.ts — shared Playwright test fixtures.
//
// HERMETIC NETWORK: the Django base template (templates/base.html) loads styling/JS from
// external CDNs (cdn.tailwindcss.com, unpkg, Google Fonts, Material Symbols) via BLOCKING
// <script>/<link> tags in <head>. Under the headless Playwright browser those external
// hosts are often unreachable (sandboxed egress) and a blocking head <script> that never
// loads prevents `DOMContentLoaded` from firing — so `page.goto` hangs.
//
// We abort every cross-origin request up front: the harness exercises OUR app (same-origin
// HTML + the local /static/ island bundle), and the CDN assets are purely cosmetic (Tailwind
// utility classes still apply structurally enough for our text/role assertions; the islands
// are bundled locally and need no CDN). This makes the suite fast and deterministic and frees
// it from any network dependency. Specs import { test, expect } from this module.
// ============================================================================
import { test as base, expect } from "@playwright/test";

export const test = base.extend({
  // Auto-applied to every test: drop external requests so blocking head scripts don't
  // stall DOMContentLoaded. Same-origin (the Django server) + the local static bundle pass.
  page: async ({ page, baseURL }, use) => {
    const origin = new URL(baseURL ?? "http://localhost:8099").origin;
    await page.route("**/*", (route) => {
      const url = route.request().url();
      if (url.startsWith(origin) || url.startsWith("data:") || url.startsWith("blob:")) {
        return route.continue();
      }
      // Cross-origin (CDNs, fonts) → abort so it can't block load events.
      return route.abort();
    });
    await use(page);
  },
});

export { expect };
