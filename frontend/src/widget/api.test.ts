// ============================================================================
// WidgetApi: tenant-prefixed URL construction + request bodies for the 3 public
// widget endpoints (apps/actions/public_*). Mocked fetch — no real network.
// ============================================================================
import { describe, expect, it, vi } from "vitest";

import { WidgetApi } from "./api";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function fetchMock(impl: (...args: Parameters<typeof fetch>) => ReturnType<typeof fetch>) {
  return vi.fn<Parameters<typeof fetch>, ReturnType<typeof fetch>>(impl);
}

function makeApi(fetchImpl: typeof fetch, apiHost = "") {
  return new WidgetApi({
    apiHost,
    apiBase: "/api/v1",
    orgSlug: "acme",
    environmentId: "env-9",
    fetchImpl,
  });
}

describe("WidgetApi — URLs + bodies", () => {
  it("GET app-surveys hits the tenant + env prefixed path with userId", async () => {
    const fetchImpl = fetchMock(async () => jsonResponse({ userId: "u1", person_id: "p", surveys: [] }));
    await makeApi(fetchImpl as unknown as typeof fetch).getAppSurveys("u1");
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/public/environments/env-9/app-surveys?userId=u1");
    expect((init as RequestInit).method).toBe("GET");
  });

  it("POST track sends {userId, action[, attributes, url]} to the track path", async () => {
    const fetchImpl = fetchMock(async () =>
      jsonResponse({ userId: "u1", person_id: "p", action: "a", matched_action: true, surveys: [] }),
    );
    await makeApi(fetchImpl as unknown as typeof fetch).track("u1", "signed_up", { plan: "pro" }, "http://x/y");
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/public/environments/env-9/actions/track");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      userId: "u1",
      action: "signed_up",
      attributes: { plan: "pro" },
      url: "http://x/y",
    });
  });

  it("POST track omits empty attributes/url", async () => {
    const fetchImpl = fetchMock(async () =>
      jsonResponse({ userId: "u1", person_id: "p", action: "a", matched_action: false, surveys: [] }),
    );
    await makeApi(fetchImpl as unknown as typeof fetch).track("u1", "page_view");
    const body = JSON.parse((fetchImpl.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ userId: "u1", action: "page_view" });
  });

  it("POST display hits the per-survey displays path with {userId}", async () => {
    const fetchImpl = fetchMock(async () =>
      jsonResponse({ display_id: "d1", person_id: "p" }, { status: 201 }),
    );
    await makeApi(fetchImpl as unknown as typeof fetch).recordDisplay("sv-7", "u1");
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/public/environments/env-9/app-surveys/sv-7/displays");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ userId: "u1" });
  });

  it("folds a cross-origin apiHost into the URL", async () => {
    const fetchImpl = fetchMock(async () => jsonResponse({ userId: "u1", person_id: "p", surveys: [] }));
    await makeApi(fetchImpl as unknown as typeof fetch, "https://api.opensurvey.com").getAppSurveys("u1");
    expect(fetchImpl.mock.calls[0][0]).toBe(
      "https://api.opensurvey.com/api/v1/acme/public/environments/env-9/app-surveys?userId=u1",
    );
  });

  it("throws on a non-OK response", async () => {
    const fetchImpl = fetchMock(async () => jsonResponse({ detail: "boom" }, { status: 500 }));
    await expect(makeApi(fetchImpl as unknown as typeof fetch).getAppSurveys("u1")).rejects.toThrow(/500/);
  });
});
