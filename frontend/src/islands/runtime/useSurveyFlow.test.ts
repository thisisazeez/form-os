// ============================================================================
// Tests for hidden-field URL capture in useSurveyFlow.
//
// On survey load the hook reads window.location.search and captures any declared
// survey.hiddenFields[].id values into the answer map (so recall/piping + logic can
// read them). Explicit data-prefill wins over a URL value for the same key.
// ============================================================================
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PublicResponseHandle, Survey } from "../../lib/types";
import type { RuntimeApi } from "./api";
import { useSurveyFlow } from "./useSurveyFlow";

function fakeApi(survey: Survey): RuntimeApi {
  const handle: PublicResponseHandle = { id: "r1", finished: false, edit_token: "t1" };
  return {
    getSurvey: vi.fn(async () => survey),
    createResponse: vi.fn(async () => handle),
    patchResponse: vi.fn(async () => handle),
    finalizeResponse: vi.fn(async () => ({ ...handle, finished: true })),
  } as unknown as RuntimeApi;
}

function surveyWithHidden(): Survey {
  return {
    id: "s1",
    name: "Hidden fields survey",
    languages: [{ code: "en", label: "English", default: true }],
    hiddenFields: [{ id: "userId", name: "User ID" }, { id: "plan" }],
    questions: [
      {
        id: "q1",
        type: "openText",
        required: false,
        headline: { default: "Hi #recall:userId/fallback:there#" },
        config: { inputType: "short" },
      },
    ],
    endings: [{ id: "end1", type: "endScreen", headline: { default: "Thanks!" } }],
  };
}

// Stub window.location.search per test.
function setSearch(search: string) {
  Object.defineProperty(window, "location", {
    value: { ...window.location, search },
    writable: true,
    configurable: true,
  });
}

describe("useSurveyFlow — hidden-field URL capture", () => {
  const origLocation = window.location;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      value: origLocation,
      writable: true,
      configurable: true,
    });
  });

  it("captures declared hidden-field values from the URL into answers", async () => {
    setSearch("?userId=123&plan=pro");
    const { result } = renderHook(() =>
      useSurveyFlow({ api: fakeApi(surveyWithHidden()), initialSurvey: surveyWithHidden() }),
    );
    await waitFor(() => expect(result.current.phase.kind).toBe("question"));
    expect(result.current.answers.userId).toBe("123");
    expect(result.current.answers.plan).toBe("pro");
  });

  it("ignores URL params that are not declared hidden fields", async () => {
    setSearch("?userId=123&utm_source=spam&ghost=x");
    const { result } = renderHook(() =>
      useSurveyFlow({ api: fakeApi(surveyWithHidden()), initialSurvey: surveyWithHidden() }),
    );
    await waitFor(() => expect(result.current.phase.kind).toBe("question"));
    expect(result.current.answers.userId).toBe("123");
    // Undeclared params must NOT be captured (they'd be rejected server-side too).
    expect(result.current.answers.utm_source).toBeUndefined();
    expect(result.current.answers.ghost).toBeUndefined();
  });

  it("captures only the declared fields present in the URL (partial)", async () => {
    setSearch("?plan=enterprise");
    const { result } = renderHook(() =>
      useSurveyFlow({ api: fakeApi(surveyWithHidden()), initialSurvey: surveyWithHidden() }),
    );
    await waitFor(() => expect(result.current.phase.kind).toBe("question"));
    expect(result.current.answers.plan).toBe("enterprise");
    expect(result.current.answers.userId).toBeUndefined();
  });

  it("explicit prefill wins over a URL value for the same key", async () => {
    setSearch("?userId=fromUrl");
    const { result } = renderHook(() =>
      useSurveyFlow({
        api: fakeApi(surveyWithHidden()),
        initialSurvey: surveyWithHidden(),
        prefill: { userId: "fromPrefill" },
      }),
    );
    await waitFor(() => expect(result.current.phase.kind).toBe("question"));
    expect(result.current.answers.userId).toBe("fromPrefill");
  });

  it("skips empty URL values (treated as not supplied)", async () => {
    setSearch("?userId=&plan=pro");
    const { result } = renderHook(() =>
      useSurveyFlow({ api: fakeApi(surveyWithHidden()), initialSurvey: surveyWithHidden() }),
    );
    await waitFor(() => expect(result.current.phase.kind).toBe("question"));
    expect(result.current.answers.userId).toBeUndefined();
    expect(result.current.answers.plan).toBe("pro");
  });

  it("no hidden fields declared → no capture, answers start empty", async () => {
    setSearch("?userId=123");
    const plainSurvey: Survey = { ...surveyWithHidden(), hiddenFields: [] };
    const { result } = renderHook(() =>
      useSurveyFlow({ api: fakeApi(plainSurvey), initialSurvey: plainSurvey }),
    );
    await waitFor(() => expect(result.current.phase.kind).toBe("question"));
    expect(result.current.answers.userId).toBeUndefined();
    expect(Object.keys(result.current.answers)).toHaveLength(0);
  });
});
