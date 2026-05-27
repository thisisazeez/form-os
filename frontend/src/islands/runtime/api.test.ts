// ============================================================================
// RuntimeApi client tests: URL construction (tenant prefix + query tokens),
// request bodies, and HTTP-status -> ApiError.kind classification.
// ============================================================================
import { describe, expect, it, vi } from "vitest";

import { ApiError, RuntimeApi } from "./api";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

/** A typed fetch mock so `.mock.calls[0]` is a [input, init] tuple, not []. */
function fetchMock(impl: (...args: Parameters<typeof fetch>) => ReturnType<typeof fetch>) {
  return vi.fn<Parameters<typeof fetch>, ReturnType<typeof fetch>>(impl);
}

function makeApi(fetchImpl: typeof fetch) {
  return new RuntimeApi({ apiBase: "/api/v1", orgSlug: "acme", surveyId: "sv-1", fetchImpl });
}

describe("RuntimeApi — URLs + bodies", () => {
  it("GET survey hits the tenant-prefixed public path with link_token", async () => {
    const fetchImpl = fetchMock(async () => jsonResponse({ id: "sv-1", questions: [], languages: [] }));
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await api.getSurvey("tok-abc");
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/public/surveys/sv-1?link_token=tok-abc");
    expect((init as RequestInit).method).toBe("GET");
  });

  it("createResponse POSTs {data, language, link_token} to the responses path", async () => {
    const fetchImpl = fetchMock(async () => jsonResponse({ id: "r1", finished: false, edit_token: "e1" }, { status: 201 }));
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await api.createResponse({ q1: "hi" }, "en", "tok-1");
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/public/surveys/sv-1/responses");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      data: { q1: "hi" },
      language: "en",
      link_token: "tok-1",
    });
  });

  it("patchResponse PATCHes with the edit_token query param", async () => {
    const fetchImpl = fetchMock(async () => jsonResponse({ id: "r1", finished: false, edit_token: "e1" }));
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await api.patchResponse("r1", "edit-9", { q2: 5 }, "en");
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/public/responses/r1?edit_token=edit-9");
    expect((init as RequestInit).method).toBe("PATCH");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ data: { q2: 5 }, language: "en" });
  });

  it("finalizeResponse POSTs to the finalize path with edit_token", async () => {
    const fetchImpl = fetchMock(async () => jsonResponse({ id: "r1", finished: true, edit_token: "e1" }));
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await api.finalizeResponse("r1", "edit-9");
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/public/responses/r1/finalize?edit_token=edit-9");
    expect((init as RequestInit).method).toBe("POST");
  });
});

describe("RuntimeApi — error classification", () => {
  const cases: Array<[number, ApiError["kind"]]> = [
    [404, "not_found"],
    [410, "gone"],
    [429, "rate_limited"],
    [403, "forbidden"],
    [400, "validation"],
    [500, "server"],
  ];

  for (const [status, kind] of cases) {
    it(`maps HTTP ${status} -> ${kind}`, async () => {
      const fetchImpl = vi.fn(async () => new Response("", { status }));
      const api = makeApi(fetchImpl as unknown as typeof fetch);
      await expect(api.getSurvey()).rejects.toMatchObject({ status, kind });
    });
  }

  it("maps a thrown fetch (offline) -> network", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("offline");
    });
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await expect(api.getSurvey()).rejects.toMatchObject({ kind: "network" });
  });

  it("captures Retry-After on 429", async () => {
    const fetchImpl = vi.fn(async () => new Response("", { status: 429, headers: { "Retry-After": "30" } }));
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await expect(api.getSurvey()).rejects.toMatchObject({ kind: "rate_limited", retryAfter: 30 });
  });
});
