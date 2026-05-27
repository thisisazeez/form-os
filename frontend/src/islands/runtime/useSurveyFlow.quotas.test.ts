// ============================================================================
// useSurveyFlow OVER-QUOTA routing tests (feat/parity-quotas).
//
// When the finalize handle reports over_quota, the SERVER's decision OVERRIDES the logic-computed
// ending: route to the quota's ending (when quota_full_ending_id resolves to a real ending) or a
// built-in 'quota full' screen (when it is null / unresolvable). When over_quota is absent/false
// the hook takes the normal logic-computed step — byte-identical to today.
// ============================================================================
import { renderHook, act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PublicResponseHandle, Survey } from "../../lib/types";
import type { RuntimeApi } from "./api";
import { useSurveyFlow } from "./useSurveyFlow";

function singleQuestionSurvey(): Survey {
  return {
    id: "s1",
    name: "Quota survey",
    languages: [{ code: "en", label: "English", default: true }],
    questions: [
      {
        id: "q1",
        type: "openText",
        required: false,
        headline: { default: "Anything?" },
        config: { inputType: "short" },
      },
    ],
    endings: [
      { id: "end1", type: "endScreen", headline: { default: "Thanks!" } },
      { id: "quota_full", type: "endScreen", headline: { default: "We are full" } },
    ],
  };
}

/** A fake api whose finalize returns a fixed handle (lets us inject over_quota state). */
function fakeApi(survey: Survey, finalizeHandle: Partial<PublicResponseHandle>): RuntimeApi {
  const base: PublicResponseHandle = { id: "r1", finished: false, edit_token: "t1" };
  return {
    getSurvey: vi.fn(async () => survey),
    createResponse: vi.fn(async () => base),
    patchResponse: vi.fn(async () => base),
    finalizeResponse: vi.fn(async () => ({ ...base, finished: true, ...finalizeHandle })),
  } as unknown as RuntimeApi;
}

/** Advance the single-question survey to its terminal phase (answers q1, then next()). */
async function runToEnd(survey: Survey, finalizeHandle: Partial<PublicResponseHandle>) {
  const { result } = renderHook(() =>
    useSurveyFlow({ api: fakeApi(survey, finalizeHandle), initialSurvey: survey }),
  );
  await waitFor(() => expect(result.current.phase.kind).toBe("question"));
  act(() => result.current.setAnswer("q1", "hello"));
  await act(async () => {
    await result.current.next();
  });
  return result;
}

describe("useSurveyFlow — over-quota routing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("routes an over-quota respondent to the quota's ending when it resolves", async () => {
    const result = await runToEnd(singleQuestionSurvey(), {
      over_quota: true,
      quota_full_ending_id: "quota_full",
    });
    expect(result.current.phase.kind).toBe("ending");
    expect((result.current.phase as { endingId: string }).endingId).toBe("quota_full");
  });

  it("shows the built-in quota-full screen when over-quota with no ending", async () => {
    const result = await runToEnd(singleQuestionSurvey(), {
      over_quota: true,
      quota_full_ending_id: null,
    });
    expect(result.current.phase.kind).toBe("quotaFull");
  });

  it("falls back to the built-in screen when the over-quota ending no longer resolves", async () => {
    const result = await runToEnd(singleQuestionSurvey(), {
      over_quota: true,
      quota_full_ending_id: "deleted_ending",
    });
    expect(result.current.phase.kind).toBe("quotaFull");
  });

  it("takes the normal logic-computed ending when not over-quota (no quotas)", async () => {
    const result = await runToEnd(singleQuestionSurvey(), {
      over_quota: false,
      quota_full_ending_id: null,
    });
    // Falling off the end resolves to the FIRST ending (end1), not the quota one.
    expect(result.current.phase.kind).toBe("ending");
    expect((result.current.phase as { endingId: string }).endingId).toBe("end1");
  });

  it("takes the normal ending when the handle omits the quota fields entirely", async () => {
    const result = await runToEnd(singleQuestionSurvey(), {});
    expect(result.current.phase.kind).toBe("ending");
    expect((result.current.phase as { endingId: string }).endingId).toBe("end1");
  });
});
