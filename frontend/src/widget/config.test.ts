// ============================================================================
// Widget config resolution from the <script> tag.
// ============================================================================
import { afterEach, describe, expect, it } from "vitest";

import { findWidgetScript, resolveConfig } from "./config";

function script(attrs: Record<string, string>): HTMLScriptElement {
  const el = document.createElement("script");
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

afterEach(() => {
  document.head.innerHTML = "";
  document.body.innerHTML = "";
});

describe("resolveConfig", () => {
  it("reads the required env id + org slug and defaults the rest", () => {
    const cfg = resolveConfig(
      script({ "data-environment-id": "env-1", "data-org-slug": "acme" }),
    );
    expect(cfg).not.toBeNull();
    expect(cfg!.environmentId).toBe("env-1");
    expect(cfg!.orgSlug).toBe("acme");
    expect(cfg!.apiBase).toBe("/api/v1");
    expect(cfg!.pageviewAction).toBe("page_view");
    expect(cfg!.disablePageview).toBe(false);
    expect(cfg!.clickAction).toBeNull();
  });

  it("returns null when a required attribute is missing", () => {
    expect(resolveConfig(script({ "data-environment-id": "env-1" }))).toBeNull();
    expect(resolveConfig(script({ "data-org-slug": "acme" }))).toBeNull();
  });

  it("defaults apiHost to the script src origin and allows data-api-host override", () => {
    const fromSrc = resolveConfig(
      script({
        "data-environment-id": "e",
        "data-org-slug": "acme",
        src: "https://app.onform.com/static/widget/opensurvey-widget.js",
      }),
    );
    expect(fromSrc!.apiHost).toBe("https://app.onform.com");

    const override = resolveConfig(
      script({
        "data-environment-id": "e",
        "data-org-slug": "acme",
        src: "https://cdn.example.com/opensurvey-widget.js",
        "data-api-host": "https://api.onform.com/",
      }),
    );
    expect(override!.apiHost).toBe("https://api.onform.com");
  });

  it("reads the optional pageview / click trigger config", () => {
    const cfg = resolveConfig(
      script({
        "data-environment-id": "e",
        "data-org-slug": "acme",
        "data-pageview-action": "viewed_page",
        "data-page-url-match": "/pricing*",
        "data-click-action": "clicked_upgrade",
        "data-click-selector": "#upgrade-btn",
        "data-disable-pageview": "true",
      }),
    );
    expect(cfg!.pageviewAction).toBe("viewed_page");
    expect(cfg!.pageUrlMatch).toBe("/pricing*");
    expect(cfg!.clickAction).toBe("clicked_upgrade");
    expect(cfg!.clickSelector).toBe("#upgrade-btn");
    expect(cfg!.disablePageview).toBe(true);
  });
});

describe("findWidgetScript", () => {
  it("finds the script by its src filename", () => {
    const s = script({
      "data-environment-id": "e",
      "data-org-slug": "acme",
      src: "/static/widget/opensurvey-widget.js",
    });
    document.body.appendChild(s);
    expect(findWidgetScript()).toBe(s);
  });

  it("falls back to a script carrying data-environment-id", () => {
    const s = script({ "data-environment-id": "e", "data-org-slug": "acme" });
    document.body.appendChild(s);
    expect(findWidgetScript()).toBe(s);
  });
});
