// ============================================================================
// renderSurvey: mounts the REUSED runtime (SurveyRuntime + RuntimeApi) inside an
// injected shadow root, loads the definition via the public survey GET, and runs
// the normal response create → finalize loop on submit. Mocked fetch (jsdom).
//
// This proves the widget reuses the existing runtime for rendering + submission
// rather than reimplementing it.
// ============================================================================
import { fireEvent, waitFor } from "@testing-library/dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderSurvey } from "./render";
import type { WidgetConfig } from "./types";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

/** A minimal single (optional) open-text survey so we can reach finalize in one step. */
const SURVEY_DEF = {
  id: "sv-1",
  name: "Quick feedback",
  languages: [{ code: "en", label: "English", default: true }],
  questions: [
    {
      id: "q1",
      type: "openText",
      required: false,
      headline: { default: "Any thoughts?" },
      config: { inputType: "short" },
    },
  ],
  endings: [{ id: "end1", type: "endScreen", headline: { default: "Thanks!" } }],
};

const CONFIG: WidgetConfig = {
  environmentId: "env-9",
  orgSlug: "acme",
  apiHost: "",
  apiBase: "/api/v1",
  pageviewAction: "page_view",
  pageUrlMatch: null,
  clickAction: null,
  clickSelector: null,
  disablePageview: false,
};

afterEach(() => {
  document.body.innerHTML = "";
});

describe("renderSurvey — reuses the runtime in shadow DOM", () => {
  it("loads the definition via the public survey GET and mounts the survey", async () => {
    const calls: { url: string; method: string }[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method ?? "GET" });
      return jsonResponse(SURVEY_DEF);
    });

    const handle = renderSurvey({
      config: CONFIG,
      surveyId: "sv-1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    // The runtime fetches the definition from the tenant-prefixed public survey GET.
    await waitFor(() => {
      expect(calls.some((c) => c.url === "/api/v1/acme/public/surveys/sv-1" && c.method === "GET")).toBe(true);
    });

    // Mounted inside a shadow root attached to the document body.
    const host = document.querySelector("[data-opensurvey-widget='sv-1']") as HTMLElement;
    expect(host).toBeTruthy();
    expect(host.shadowRoot).toBeTruthy();

    // The reused runtime rendered the question headline into the shadow root.
    await waitFor(() => {
      expect(host.shadowRoot!.textContent).toContain("Any thoughts?");
    });

    handle.dismiss();
    expect(document.querySelector("[data-opensurvey-widget='sv-1']")).toBeNull();
  });

  it("runs the response create → finalize loop on submit", async () => {
    const calls: { url: string; method: string }[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url.endsWith("/public/surveys/sv-1") && method === "GET") return jsonResponse(SURVEY_DEF);
      if (url.endsWith("/public/surveys/sv-1/responses")) {
        return jsonResponse({ id: "r1", finished: false, edit_token: "e1" }, { status: 201 });
      }
      if (url.includes("/public/responses/r1/finalize")) {
        return jsonResponse({ id: "r1", finished: true, edit_token: "e1" });
      }
      if (url.includes("/public/responses/r1")) {
        return jsonResponse({ id: "r1", finished: false, edit_token: "e1" });
      }
      return jsonResponse({}, { status: 404 });
    });

    renderSurvey({
      config: CONFIG,
      surveyId: "sv-1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    const host = await waitFor(() => {
      const h = document.querySelector("[data-opensurvey-widget='sv-1']") as HTMLElement;
      if (!h?.shadowRoot?.textContent?.includes("Any thoughts?")) throw new Error("not mounted yet");
      return h;
    });

    // Advance the (optional) question — the primary button submits/finishes the survey.
    const buttons = Array.from(host.shadowRoot!.querySelectorAll("button"));
    const submit = buttons.find((b) => /finish|submit|next|done|continue/i.test(b.textContent ?? ""));
    expect(submit).toBeTruthy();
    fireEvent.click(submit!);

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("/public/responses/r1/finalize"))).toBe(true);
    });
    // create happened before finalize.
    expect(calls.some((c) => c.url.endsWith("/public/surveys/sv-1/responses"))).toBe(true);
  });
});
