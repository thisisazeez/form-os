// ============================================================================
// boot(): the entrypoint reads data-* from the widget <script> tag, builds the
// runtime, exposes window.openSurvey { init, setUserId, track, reset }, and is a
// no-op singleton on re-run. Mocked fetch (no network); the heavy render path is
// covered in render.test.tsx, so here we only assert the public API + boot wiring.
// ============================================================================
import "./test-storage"; // install in-memory Web Storage on jsdom globals (side effect)

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { boot } from "./main";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  document.body.innerHTML = "";
  delete (window as { openSurvey?: unknown }).openSurvey;
  // The widget warms the catalog on init; stub fetch so it resolves.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ userId: "u", person_id: "p", surveys: [] })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function placeScript(attrs: Record<string, string>) {
  const s = document.createElement("script");
  for (const [k, v] of Object.entries(attrs)) s.setAttribute(k, v);
  s.setAttribute("src", "/static/widget/opensurvey-widget.js");
  document.body.appendChild(s);
  return s;
}

describe("boot", () => {
  it("reads data-* and exposes the public API on window.openSurvey", () => {
    placeScript({ "data-environment-id": "env-1", "data-org-slug": "acme" });
    const api = boot();
    expect(window.openSurvey).toBe(api);
    expect(api.ready).toBe(true);
    expect(typeof api.init).toBe("function");
    expect(typeof api.setUserId).toBe("function");
    expect(typeof api.track).toBe("function");
    expect(typeof api.reset).toBe("function");
  });

  it("persists a userId on boot", () => {
    placeScript({ "data-environment-id": "env-1", "data-org-slug": "acme" });
    boot();
    expect(localStorage.getItem("openSurvey.userId")).toBeTruthy();
  });

  it("is a no-op singleton when re-run", () => {
    placeScript({ "data-environment-id": "env-1", "data-org-slug": "acme" });
    const first = boot();
    const second = boot();
    expect(second).toBe(first);
  });

  it("disables itself (no throw) when the required data-* are missing", () => {
    placeScript({ "data-environment-id": "env-1" }); // no org slug
    const api = boot();
    expect(api.ready).toBe(false);
    // The no-op shell tolerates calls without throwing.
    expect(() => {
      api.setUserId("x");
      api.track("y");
      api.reset();
      api.init();
    }).not.toThrow();
  });
});
