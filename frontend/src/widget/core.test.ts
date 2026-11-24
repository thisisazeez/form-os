// ============================================================================
// Widget CORE: trigger wiring (pageview / click / code) → track → de-dup →
// display → render. Mocked fetch + an injected render stub (the real renderSurvey
// is exercised separately in render.test.tsx). Verifies the task contract:
//   - init generates/persists a userId and warms the catalog
//   - pageview fires track(<pageviewAction>) on load + SPA pushState/popstate
//   - click fires track(<clickAction>) on a matching delegated click
//   - code: openSurvey.track(name, props) sends the right body
//   - a returned survey is rendered, its display recorded, and NOT re-shown in-session
// ============================================================================
import "./test-storage"; // install in-memory Web Storage on jsdom globals (side effect)

import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { OnFormWidget } from "./core";
import type { RenderedSurvey, RenderOptions } from "./render";
import { WidgetStorage } from "./storage";
import type { CatalogSurvey, WidgetConfig } from "./types";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

const SURVEY: CatalogSurvey = {
  id: "sv-1",
  name: "NPS",
  survey_type: "app",
  triggers: ["act-1"],
  display_options: {},
};

interface Harness {
  cs: OnFormWidget;
  storage: WidgetStorage;
  calls: () => { url: string; method: string; body: unknown }[];
  rendered: string[];
  fetchMock: Mock<[RequestInfo | URL, (RequestInit | undefined)?], Promise<Response>>;
}

/** Build an OnFormWidget with a routed fetch mock + a render stub. */
function makeHarness(opts: {
  config?: Partial<WidgetConfig>;
  trackSurveys?: CatalogSurvey[];
  catalogSurveys?: CatalogSurvey[];
} = {}): Harness {
  const recorded: { url: string; method: string; body: unknown }[] = [];
  const rendered: string[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    recorded.push({ url, method, body });

    if (url.includes("/app-surveys?")) {
      return jsonResponse({ userId: "u", person_id: "p", surveys: opts.catalogSurveys ?? [] });
    }
    if (url.endsWith("/actions/track")) {
      return jsonResponse({
        userId: "u",
        person_id: "p",
        action: body?.action,
        matched_action: true,
        surveys: opts.trackSurveys ?? [],
      });
    }
    if (url.includes("/displays")) {
      return jsonResponse({ display_id: "d1", person_id: "p" }, { status: 201 });
    }
    return jsonResponse({}, { status: 404 });
  });

  const config: WidgetConfig = {
    environmentId: "env-9",
    orgSlug: "acme",
    apiHost: "",
    apiBase: "/api/v1",
    pageviewAction: "page_view",
    pageUrlMatch: null,
    clickAction: null,
    clickSelector: null,
    disablePageview: false,
    ...opts.config,
  };

  const storage = new WidgetStorage();

  const renderStub = (ro: RenderOptions): RenderedSurvey => {
    rendered.push(ro.surveyId);
    return { surveyId: ro.surveyId, dismiss: () => {} };
  };

  const cs = new OnFormWidget(config, {
    fetchImpl: fetchMock as unknown as typeof fetch,
    storage,
    render: renderStub,
  });

  return { cs, storage, calls: () => recorded, rendered, fetchMock };
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("onForm.init", () => {
  it("generates + persists a userId and warms the catalog", async () => {
    const h = makeHarness();
    await h.cs.init();
    const uid = h.storage.getUserId();
    expect(uid).toBeTruthy();
    const warm = h.calls().find((c) => c.url.includes("/app-surveys?"));
    expect(warm?.url).toContain(`userId=${uid}`);
  });

  it("fires the initial pageview track on init", async () => {
    const h = makeHarness();
    await h.cs.init();
    const track = h.calls().filter((c) => c.url.endsWith("/actions/track"));
    expect(track.length).toBe(1);
    expect((track[0].body as { action: string }).action).toBe("page_view");
  });

  it("does not fire pageview when disablePageview is set", async () => {
    const h = makeHarness({ config: { disablePageview: true } });
    await h.cs.init();
    expect(h.calls().filter((c) => c.url.endsWith("/actions/track")).length).toBe(0);
  });
});

describe("pageview trigger", () => {
  it("fires track on SPA pushState navigation", async () => {
    const h = makeHarness();
    await h.cs.init();
    history.pushState({}, "", "/pricing");
    await Promise.resolve(); // let the async fire() settle
    const track = h.calls().filter((c) => c.url.endsWith("/actions/track"));
    // one from init + one from the pushState navigation
    expect(track.length).toBe(2);
    h.cs.destroy();
  });

  it("honors a client-side data-page-url-match pre-filter", async () => {
    const h = makeHarness({ config: { pageUrlMatch: "/pricing*", disablePageview: false } });
    // location is the jsdom default (http://localhost/) — does NOT match /pricing*
    await h.cs.init();
    expect(h.calls().filter((c) => c.url.endsWith("/actions/track")).length).toBe(0);
    h.cs.destroy();
  });
});

describe("click trigger", () => {
  it("fires track(<clickAction>) on a matching delegated click", async () => {
    const h = makeHarness({
      config: { disablePageview: true, clickAction: "clicked_upgrade", clickSelector: "#up" },
    });
    await h.cs.init();
    const btn = document.createElement("button");
    btn.id = "up";
    document.body.appendChild(btn);
    btn.click();
    await Promise.resolve();
    const track = h.calls().filter((c) => c.url.endsWith("/actions/track"));
    expect(track.length).toBe(1);
    expect((track[0].body as { action: string }).action).toBe("clicked_upgrade");
    h.cs.destroy();
    btn.remove();
  });

  it("ignores clicks that do not match the selector", async () => {
    const h = makeHarness({
      config: { disablePageview: true, clickAction: "clicked_upgrade", clickSelector: "#up" },
    });
    await h.cs.init();
    const other = document.createElement("button");
    document.body.appendChild(other);
    other.click();
    await Promise.resolve();
    expect(h.calls().filter((c) => c.url.endsWith("/actions/track")).length).toBe(0);
    h.cs.destroy();
    other.remove();
  });
});

describe("code trigger (.track)", () => {
  it("sends the action name + props in the track body", async () => {
    const h = makeHarness({ config: { disablePageview: true } });
    await h.cs.init();
    await h.cs.track("signed_up", { plan: "pro" });
    const track = h.calls().find((c) => c.url.endsWith("/actions/track"));
    expect(track!.body).toMatchObject({ action: "signed_up", attributes: { plan: "pro" } });
  });
});

describe("survey display + render + in-session de-dup", () => {
  it("renders a returned survey and records its display once", async () => {
    const h = makeHarness({ config: { disablePageview: true }, trackSurveys: [SURVEY] });
    await h.cs.init();
    await h.cs.track("signed_up");

    expect(h.rendered).toEqual(["sv-1"]);
    const displays = h.calls().filter((c) => c.url.includes("/displays"));
    expect(displays.length).toBe(1);
    expect(displays[0].url).toContain("/app-surveys/sv-1/displays");
    expect(displays[0].body).toEqual({ userId: h.storage.getUserId() });
  });

  it("does NOT re-show a survey already shown this session", async () => {
    const h = makeHarness({ config: { disablePageview: true }, trackSurveys: [SURVEY] });
    await h.cs.init();
    await h.cs.track("signed_up"); // shows sv-1
    await h.cs.track("signed_up"); // should be de-duped
    expect(h.rendered).toEqual(["sv-1"]);
    expect(h.calls().filter((c) => c.url.includes("/displays")).length).toBe(1);
  });

  it("uses setUserId for subsequent track/display calls", async () => {
    const h = makeHarness({ config: { disablePageview: true }, trackSurveys: [SURVEY] });
    await h.cs.init();
    h.cs.setUserId("crm-42");
    await h.cs.track("signed_up");
    const track = h.calls().find((c) => c.url.endsWith("/actions/track"));
    expect((track!.body as { userId: string }).userId).toBe("crm-42");
    expect(h.storage.getUserId()).toBe("crm-42");
  });

  it("a tracking failure never throws into the host page", async () => {
    const h = makeHarness({ config: { disablePageview: true } });
    h.fetchMock.mockImplementationOnce(async () => {
      throw new Error("network down");
    });
    await h.cs.init();
    await expect(h.cs.track("signed_up")).resolves.toBeUndefined();
  });
});

describe("reset", () => {
  it("clears the userId and the in-session de-dup set", async () => {
    const h = makeHarness({ config: { disablePageview: true }, trackSurveys: [SURVEY] });
    await h.cs.init();
    await h.cs.track("signed_up");
    const before = h.storage.getUserId();

    h.cs.reset();
    const after = h.storage.getUserId();
    expect(after).not.toBe(before);

    // The same survey can show again for the new visitor.
    await h.cs.track("signed_up");
    expect(h.rendered).toEqual(["sv-1", "sv-1"]);
  });
});
