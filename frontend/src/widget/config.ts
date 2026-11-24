// ============================================================================
// onForm widget — config resolution from the <script> tag.
//
// The embed contract (must match the admin settings page):
//   <script src="<APP_ORIGIN>/static/widget/opensurvey-widget.js"
//           data-environment-id="<ENV_ID>"
//           data-org-slug="<ORG_SLUG>"></script>
//
// data-org-slug is REQUIRED because the public widget APIs are tenant-prefixed
// (`/api/v1/<org_slug>/public/...`) and TenantSlugMiddleware resolves the org schema
// from that slug — there is NO env-only public route. The API host defaults to the
// widget script's OWN origin (so a same-origin embed needs nothing extra); override
// with data-api-host for a cross-origin embed.
// ============================================================================
import type { WidgetConfig } from "./types";

/** Read a data-* attribute, trimmed; "" → null for optional values. */
function attr(el: Element, name: string): string | null {
  const v = el.getAttribute(name);
  if (v === null) return null;
  const t = v.trim();
  return t === "" ? null : t;
}

/** The origin of a script src URL (so the API host defaults to where the widget came from). */
function originOf(src: string | null): string {
  if (!src) return "";
  try {
    return new URL(src, currentHref()).origin;
  } catch {
    return "";
  }
}

function currentHref(): string {
  try {
    return window.location.href;
  } catch {
    return "http://localhost/";
  }
}

/**
 * Resolve the widget config from a <script> element's data-* attributes. Returns null
 * (with a console warning) if a REQUIRED attribute is missing — the widget then no-ops
 * rather than throwing into the host page.
 */
export function resolveConfig(script: Element): WidgetConfig | null {
  const environmentId = attr(script, "data-environment-id");
  const orgSlug = attr(script, "data-org-slug");

  if (!environmentId || !orgSlug) {
    // eslint-disable-next-line no-console
    console.warn(
      "[onForm] missing required data-environment-id and/or data-org-slug on the widget <script> tag; widget disabled.",
    );
    return null;
  }

  // API host: explicit data-api-host wins; else the script's own origin; else same-origin ("").
  const explicitHost = attr(script, "data-api-host");
  const apiHost = (explicitHost ?? originOf(script.getAttribute("src"))).replace(/\/$/, "");

  return {
    environmentId,
    orgSlug,
    apiHost,
    apiBase: (attr(script, "data-api-base") ?? "/api/v1").replace(/\/$/, ""),
    pageviewAction: attr(script, "data-pageview-action") ?? "page_view",
    pageUrlMatch: attr(script, "data-page-url-match"),
    clickAction: attr(script, "data-click-action"),
    clickSelector: attr(script, "data-click-selector"),
    disablePageview: attr(script, "data-disable-pageview") === "true",
  };
}

/**
 * Find the widget's own <script> tag. Prefers document.currentScript (the executing
 * script during initial parse); falls back to the first <script> whose src points at
 * opensurvey-widget.js, then any script carrying data-environment-id.
 */
export function findWidgetScript(): Element | null {
  const cur = document.currentScript;
  if (cur && cur.getAttribute("data-environment-id")) return cur;

  const scripts = Array.from(document.querySelectorAll("script[src]"));
  const bySrc = scripts.find((s) => (s.getAttribute("src") ?? "").includes("opensurvey-widget"));
  if (bySrc) return bySrc;

  return document.querySelector("script[data-environment-id]");
}
