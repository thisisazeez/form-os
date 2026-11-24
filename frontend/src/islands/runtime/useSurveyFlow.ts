// ============================================================================
// onForm — survey flow state machine (runtime island).
//
// Drives the respondent through: welcome card -> questions (via the logic
// engine for next-step/branching + required-gating) -> ending/thank-you.
// Handles partial save (PATCH as the respondent progresses; first answer POSTs
// the response) and final submit (finalize).
//
// The logic engine (frontend/src/lib/logic) is the single source of truth for
// navigation; this hook never re-implements branching — it calls nextStep with
// the answers so far and the just-answered question id.
// ============================================================================
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isEmptyAnswer, validateAnswer } from "../../lib/answers";
import { applyBlockRandomization } from "../../lib/blocks";
import { resolveActiveLanguage } from "../../lib/i18n";
import { computeVariables, nextStep, requiredQuestionIds } from "../../lib/logic";
import type {
  AnswerMap,
  AnswerValue,
  NextStep,
  PublicResponseHandle,
  Question,
  Survey,
} from "../../lib/types";
import { ApiError, RuntimeApi } from "./api";

/**
 * A stable per-session randomization seed, frozen once for the WHOLE flow so the within-block
 * presentation permutation never changes mid-survey (back-nav stability — the brief's hard
 * requirement). Prefers `crypto.randomUUID()`; falls back to a Math.random()-based id where
 * crypto is unavailable (old/test environments). It is NOT the response id — the response is
 * created lazily on the first answer, but the permutation must be stable from the FIRST
 * question, before any response exists.
 */
function makeSessionSeed(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    /* fall through */
  }
  return `seed-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

export type Phase =
  | { kind: "loading" }
  | { kind: "error"; error: ApiError }
  | { kind: "welcome" }
  | { kind: "question"; question: Question }
  | { kind: "ending"; endingId: string }
  | { kind: "end" }
  // QUOTAS (additive): the server marked this finished response over-quota and the quota
  // declared NO endingId (or one that no longer resolves), so we show a built-in 'quota full'
  // screen rather than a survey ending. An over-quota response WITH a resolvable endingId is
  // shown as the normal `{kind:"ending"}` phase (reusing the thank-you/ending renderer).
  | { kind: "quotaFull" };

export interface UseSurveyFlowOptions {
  api: RuntimeApi;
  linkToken?: string | null;
  /** Optional pre-injected survey (skips the GET). */
  initialSurvey?: Survey | null;
  /** Requested response language (e.g. from ?lang= or the host). */
  requestedLanguage?: string | null;
  /** Hidden-field prefills (from URL query/link), merged into the answer map. */
  prefill?: AnswerMap;
}

export interface SurveyFlow {
  phase: Phase;
  survey: Survey | null;
  lang: string;
  defaultLang: string;
  answers: AnswerMap;
  /**
   * The answer map to use for RECALL/piping: the raw answers OVERLAID with the live survey
   * variable values (computeVariables). Variables share the recall id namespace, so a
   * `#recall:<varId>/...#` token resolves to its live value. Variables are NOT persisted into
   * Response.data — they are recomputed here. With no variables this is the SAME reference as
   * `answers` (recall behavior byte-identical to today).
   */
  recallAnswers: AnswerMap;
  /** Effective required set for THIS respondent (static + logic-required). */
  requiredIds: Set<string>;
  /** Whether a network op (POST/PATCH/finalize) is in flight. */
  saving: boolean;
  /** Validation message for the current question, or null. */
  validationError: string | null;
  /** Linear progress 0..1 for the progress bar (best-effort over answered path). */
  progress: number;
  /** True if there is a previous step to go back to. */
  canGoBack: boolean;

  /** Update the current question's draft answer (no network). */
  setAnswer: (questionId: string, value: AnswerValue | undefined) => void;
  /** Advance from the welcome card to the first question. */
  startSurvey: () => void;
  /** Validate + persist the current answer, then advance via the engine. */
  next: () => Promise<void>;
  /** Step back to the previous answered question. */
  back: () => void;
}

/** A skipped (key-absent) answer must not be sent as null — strip empties for the engine. */
function cleanAnswers(map: AnswerMap): AnswerMap {
  const out: AnswerMap = {};
  for (const [k, v] of Object.entries(map)) {
    if (!isEmptyAnswer(v)) out[k] = v;
  }
  return out;
}

/**
 * Capture survey-level hidden-field values from the URL query string.
 *
 * For each declared `survey.hiddenFields[].id`, if `?<id>=value` is present in the URL it
 * is captured into the response data map under that id — where recall/piping + logic can
 * read it (mirrors Formbricks' hidden-field URL prefill). Explicit server-decoded `prefill`
 * (data-prefill on the mount node) WINS over a URL value for the same key. Returns only the
 * captured entries (the caller merges them); empty values are skipped.
 */
function captureHiddenFieldsFromUrl(
  survey: Survey,
  prefill: AnswerMap | undefined,
  search: string,
): AnswerMap {
  const fields = survey.hiddenFields ?? [];
  if (fields.length === 0) return {};
  const params = new URLSearchParams(search);
  const captured: AnswerMap = {};
  for (const f of fields) {
    const id = f?.id;
    if (!id) continue;
    // Explicit prefill for this key takes precedence — don't override it from the URL.
    if (prefill && prefill[id] !== undefined) continue;
    if (!params.has(id)) continue;
    const value = params.get(id);
    if (value === null || value === "") continue;
    captured[id] = value;
  }
  return captured;
}

export function useSurveyFlow(opts: UseSurveyFlowOptions): SurveyFlow {
  const { api, linkToken, initialSurvey, requestedLanguage, prefill } = opts;

  const [survey, setSurvey] = useState<Survey | null>(initialSurvey ?? null);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [answers, setAnswers] = useState<AnswerMap>(prefill ? { ...prefill } : {});
  const [saving, setSaving] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  // The ordered trail of answered question ids — drives "back" and progress.
  const [trail, setTrail] = useState<string[]>([]);

  // Response handle (created lazily on first answer).
  const responseRef = useRef<{ id: string; editToken: string } | null>(null);

  // One frozen per-session seed for within-block presentation randomization (back-nav stable).
  const seedRef = useRef<string>(makeSessionSeed());

  // The survey HANDED TO THE LOGIC ENGINE: the loaded survey with each randomizing block's
  // member questions permuted into their flat slots (applyBlockRandomization). With no blocks
  // (or all randomize:"none") this is the SAME reference as `survey` — flat behavior unchanged.
  // The navigator resolves by id, so a permuted order is safe; only the PRESENTATION sequence
  // within a randomizing block changes.
  const flowSurvey = useMemo(
    () => (survey ? applyBlockRandomization(survey, seedRef.current) : null),
    [survey],
  );

  const lang = useMemo(
    () => resolveActiveLanguage(requestedLanguage, survey?.languages),
    [requestedLanguage, survey],
  );
  const defaultLang = useMemo(() => {
    const def = (survey?.languages ?? []).find((l) => l.default);
    return def?.code ?? "default";
  }, [survey]);

  // --- load the survey (unless pre-injected) ---
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const s = initialSurvey ?? (await api.getSurvey(linkToken));
        if (cancelled) return;
        setSurvey(s);
        // Capture declared hidden-field values from the URL (?key=value) into the answer
        // map, so recall/piping + logic can read them. Explicit data-prefill wins per key.
        const search = typeof window !== "undefined" ? window.location.search : "";
        const captured = captureHiddenFieldsFromUrl(s, prefill, search);
        const seeded: AnswerMap = { ...(prefill ?? {}), ...captured };
        if (Object.keys(captured).length > 0) {
          setAnswers(seeded);
        }
        // Welcome card if enabled, else jump straight to the first step. Navigation runs over
        // the presentation-permuted survey (identity when there are no randomizing blocks) so
        // within-block randomization is reflected from the very first question.
        const flow = applyBlockRandomization(s, seedRef.current);
        if (s.welcomeCard?.enabled === true) {
          setPhase({ kind: "welcome" });
        } else {
          setPhase(stepToPhase(flow, nextStep(flow, cleanAnswers(seeded), null)));
        }
      } catch (e) {
        if (cancelled) return;
        setPhase({ kind: "error", error: e instanceof ApiError ? e : new ApiError(0, "network") });
      }
    }
    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const requiredIds = useMemo(
    // requiredQuestionIds is order-independent (it scans questions by id), so the permuted
    // flowSurvey yields the SAME set as the flat survey — but we use flowSurvey for consistency.
    () =>
      flowSurvey ? requiredQuestionIds(flowSurvey, cleanAnswers(answers)) : new Set<string>(),
    [flowSurvey, answers],
  );

  // The recall lookup map: answers overlaid with the live variable values (computeVariables).
  // With no variables, computeVariables returns {} and recallAnswers === answers (byte-identical).
  const recallAnswers = useMemo<AnswerMap>(() => {
    if (!flowSurvey) return answers;
    const vars = computeVariables(flowSurvey, cleanAnswers(answers));
    return Object.keys(vars).length === 0 ? answers : { ...answers, ...(vars as AnswerMap) };
  }, [flowSurvey, answers]);

  const setAnswer = useCallback((questionId: string, value: AnswerValue | undefined) => {
    setValidationError(null);
    setAnswers((prev) => {
      const next = { ...prev };
      if (value === undefined) delete next[questionId];
      else next[questionId] = value;
      return next;
    });
  }, []);

  const startSurvey = useCallback(() => {
    if (!flowSurvey) return;
    setPhase(stepToPhase(flowSurvey, nextStep(flowSurvey, cleanAnswers(answers), null)));
  }, [flowSurvey, answers]);

  const next = useCallback(async () => {
    if (!flowSurvey || phase.kind !== "question") return;
    const q = phase.question;
    const value = answers[q.id];
    const isRequired = requiredIds.has(q.id);

    // Client-side gating (server re-validates on finalize).
    const err = validateAnswer(q, value, isRequired);
    if (err) {
      setValidationError(err);
      return;
    }
    setValidationError(null);

    const cleaned = cleanAnswers(answers);
    setSaving(true);
    try {
      // Persist progress: create the response on the first answer, else PATCH.
      await persist(api, responseRef, cleaned, lang, linkToken);

      // Navigate over the presentation-permuted survey: "linear next within a block" follows
      // the block's presented order; jumps/skips/conditions still resolve by id, unchanged.
      const step = nextStep(flowSurvey, cleaned, q.id);
      setTrail((t) => [...t, q.id]);

      if (step.type === "question") {
        setPhase({ kind: "question", question: questionById(flowSurvey, step.id) });
      } else {
        // Reached an ending / end-of-survey: finalize the response. The finalize handle may
        // report this response is OVER-QUOTA — if so, the server's decision OVERRIDES the
        // logic-computed ending: route to the quota's ending when it resolves to a real ending,
        // else show the built-in 'quota full' screen. With no quotas the handle carries no
        // over_quota flag and we take the normal logic-computed step (byte-identical to today).
        const handle = await finalize(api, responseRef);
        setPhase(resolveFinalPhase(flowSurvey, step, handle));
      }
    } catch (e) {
      setPhase({ kind: "error", error: e instanceof ApiError ? e : new ApiError(0, "network") });
    } finally {
      setSaving(false);
    }
  }, [flowSurvey, phase, answers, requiredIds, api, lang, linkToken]);

  const back = useCallback(() => {
    if (!flowSurvey || trail.length === 0) return;
    setValidationError(null);
    const prevId = trail[trail.length - 1];
    setTrail((t) => t.slice(0, -1));
    setPhase({ kind: "question", question: questionById(flowSurvey, prevId) });
  }, [flowSurvey, trail]);

  const progress = useMemo(() => {
    const total = survey?.questions.length ?? 0;
    if (total === 0) return 0;
    if (phase.kind === "ending" || phase.kind === "end") return 1;
    return Math.min(trail.length / total, 1);
  }, [survey, trail, phase]);

  return {
    phase,
    survey,
    lang,
    defaultLang,
    answers,
    recallAnswers,
    requiredIds,
    saving,
    validationError,
    progress,
    canGoBack: trail.length > 0 && phase.kind === "question",
    setAnswer,
    startSurvey,
    next,
    back,
  };
}

// --------------------------------------------------------------------------- //
//  Helpers                                                                    //
// --------------------------------------------------------------------------- //
function stepToPhase(survey: Survey, step: NextStep): Phase {
  if (step.type === "question") return { kind: "question", question: questionById(survey, step.id) };
  if (step.type === "ending") return { kind: "ending", endingId: step.id };
  return { kind: "end" };
}

/**
 * The terminal phase to show after a finalize. The SERVER's over-quota decision (on the finalize
 * `handle`) OVERRIDES the logic-computed `step`: an over-quota respondent is routed to the quota's
 * ending (`quota_full_ending_id`, when it resolves to a real ending) or a built-in 'quota full'
 * screen (when the quota declared no ending). When the handle is absent or not over-quota (the
 * common case + every survey with no quotas), we take the normal logic-computed step —
 * byte-identical to the pre-quota behavior.
 */
function resolveFinalPhase(
  survey: Survey,
  step: NextStep,
  handle: PublicResponseHandle | null,
): Phase {
  if (handle?.over_quota) {
    const endingId = handle.quota_full_ending_id;
    if (endingId && (survey.endings ?? []).some((e) => e.id === endingId)) {
      return { kind: "ending", endingId };
    }
    return { kind: "quotaFull" };
  }
  return stepToPhase(survey, step);
}

function questionById(survey: Survey, id: string): Question {
  const q = survey.questions.find((x) => x.id === id);
  if (!q) throw new Error(`question ${id} not found in survey`);
  return q;
}

async function persist(
  api: RuntimeApi,
  responseRef: React.MutableRefObject<{ id: string; editToken: string } | null>,
  data: AnswerMap,
  lang: string,
  linkToken?: string | null,
): Promise<void> {
  if (responseRef.current === null) {
    const handle = await api.createResponse(data, lang, linkToken);
    responseRef.current = { id: handle.id, editToken: handle.edit_token };
  } else {
    await api.patchResponse(responseRef.current.id, responseRef.current.editToken, data, lang);
  }
}

async function finalize(
  api: RuntimeApi,
  responseRef: React.MutableRefObject<{ id: string; editToken: string } | null>,
): Promise<PublicResponseHandle | null> {
  if (responseRef.current === null) return null; // nothing was answered (all-optional, no input)
  return api.finalizeResponse(responseRef.current.id, responseRef.current.editToken);
}
