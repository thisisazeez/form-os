// ============================================================================
// BuilderApi tests: PUT URL + body + CSRF header, 200 round-trip, and the
// 400 ValidationErrorBody → flat message mapping (schema_errors + errors + detail).
// ============================================================================
import { describe, expect, it, vi } from "vitest";

import { BuilderApi, SaveError, flattenValidationBody } from "./api";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function fetchMock(impl: (...a: Parameters<typeof fetch>) => ReturnType<typeof fetch>) {
  return vi.fn<Parameters<typeof fetch>, ReturnType<typeof fetch>>(impl);
}

function makeApi(fetchImpl: typeof fetch, csrfToken = "csrf-xyz") {
  return new BuilderApi({ apiBase: "/api/v1", orgSlug: "acme", surveyId: "sv-1", csrfToken, fetchImpl });
}

describe("BuilderApi — PUT", () => {
  it("PUTs the SurveyWrite body to the tenant-prefixed survey path with CSRF", async () => {
    const fetchImpl = fetchMock(async () =>
      jsonResponse({ id: "sv-1", name: "S", questions: [], languages: [] }),
    );
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    const body = { name: "S", questions: [{ id: "q1" }], languages: [{ code: "en", default: true }] };
    const saved = await api.saveSurvey(body);

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/surveys/sv-1");
    expect((init as RequestInit).method).toBe("PUT");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(body);
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBe("csrf-xyz");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(saved.id).toBe("sv-1");
  });

  it("omits the CSRF header when no token is available", async () => {
    const fetchImpl = fetchMock(async () => jsonResponse({ id: "sv-1", questions: [], languages: [] }));
    const api = makeApi(fetchImpl as unknown as typeof fetch, "");
    await api.saveSurvey({ name: "S", questions: [], languages: [] });
    const headers = (fetchImpl.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect("X-CSRFToken" in headers).toBe(false);
  });

  it("maps a 400 ValidationErrorBody to a SaveError with flattened schema + logic messages", async () => {
    const body = {
      detail: "Invalid survey.",
      schema_errors: ["questions.0.config.choices: too few items (1 < 2)"],
      errors: { logic: ["jumpTo target q9 does not exist"] },
    };
    const fetchImpl = fetchMock(async () => jsonResponse(body, { status: 400 }));
    const api = makeApi(fetchImpl as unknown as typeof fetch);

    await expect(api.saveSurvey({ name: "S", questions: [], languages: [] })).rejects.toBeInstanceOf(
      SaveError,
    );
    try {
      await api.saveSurvey({ name: "S", questions: [], languages: [] });
    } catch (e) {
      const err = e as SaveError;
      expect(err.status).toBe(400);
      expect(err.messages).toContain("questions.0.config.choices: too few items (1 < 2)");
      expect(err.messages.some((m) => m.includes("jumpTo target q9 does not exist"))).toBe(true);
    }
  });

  it("maps a 403 to a permission SaveError", async () => {
    const fetchImpl = fetchMock(async () => new Response("", { status: 403 }));
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await expect(api.saveSurvey({ name: "S", questions: [], languages: [] })).rejects.toMatchObject({
      status: 403,
    });
  });

  it("maps a thrown fetch (offline) to a network SaveError", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("offline");
    });
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await expect(api.saveSurvey({ name: "S", questions: [], languages: [] })).rejects.toMatchObject({
      status: 0,
    });
  });
});

describe("BuilderApi — status transition", () => {
  it("POSTs { status: 'published' } to /surveys/{id}/status with CSRF and returns the echo", async () => {
    const fetchImpl = fetchMock(async () =>
      jsonResponse({ id: "sv-1", status: "published", questions: [], languages: [] }),
    );
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    const saved = await api.publishSurvey();

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/acme/surveys/sv-1/status");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ status: "published" });
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBe("csrf-xyz");
    expect(saved.status).toBe("published");
  });

  it("changeStatus targets the requested status (paused / closed)", async () => {
    const fetchImpl = fetchMock(async () =>
      jsonResponse({ id: "sv-1", status: "closed", questions: [], languages: [] }),
    );
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await api.closeSurvey();
    expect(JSON.parse((fetchImpl.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      status: "closed",
    });
  });

  it("maps a 400 invalid-transition {detail} to a SaveError message", async () => {
    const fetchImpl = fetchMock(async () =>
      jsonResponse({ detail: "Cannot transition survey from 'closed' to 'published'." }, { status: 400 }),
    );
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await expect(api.publishSurvey()).rejects.toBeInstanceOf(SaveError);
    try {
      await api.publishSurvey();
    } catch (e) {
      const err = e as SaveError;
      expect(err.status).toBe(400);
      expect(err.messages[0]).toMatch(/Cannot transition survey/);
    }
  });

  it("maps a 403 to a permission SaveError", async () => {
    const fetchImpl = fetchMock(async () => new Response("", { status: 403 }));
    const api = makeApi(fetchImpl as unknown as typeof fetch);
    await expect(api.publishSurvey()).rejects.toMatchObject({ status: 403 });
  });
});

describe("flattenValidationBody", () => {
  it("collects schema_errors", () => {
    expect(flattenValidationBody({ schema_errors: ["a", "b"] })).toEqual(["a", "b"]);
  });

  it("prefixes nested errors with their field path", () => {
    const out = flattenValidationBody({ errors: { logic: ["bad rule"], name: "required" } });
    expect(out).toContain("logic: bad rule");
    expect(out).toContain("name: required");
  });

  it("falls back to detail only when nothing else is present", () => {
    expect(flattenValidationBody({ detail: "nope" })).toEqual(["nope"]);
    expect(flattenValidationBody({ schema_errors: ["x"], detail: "ignored" })).toEqual(["x"]);
  });
});
