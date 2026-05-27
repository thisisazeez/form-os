// ============================================================================
// OpenSurvey — Builder LIVE PREVIEW pane  ★ (the headline feature).
//
// Renders the current draft survey EXACTLY as a respondent sees it, by REUSING
// the shared Wave-3a runtime library directly:
//
//   - QuestionRenderer  (../../lib/questions) — the same per-type renderers the
//     public runtime uses, so preview == production rendering.
//   - nextStep / requiredQuestionIds (../../lib/logic) — the same logic engine
//     drives branching + required-gating here as in the runtime.
//   - validateAnswer (../../lib/answers) — the same advance-gating.
//   - resolveI18n / defaultLanguageCode (../../lib/i18n) — the same i18n chain.
//
// This deliberately does NOT reuse SurveyRuntime/useSurveyFlow: those are bound
// to RuntimeApi (POST/PATCH/finalize) and would require network mocking inside
// the builder. Instead we keep a LOCAL, throwaway answer map and re-derive the
// flow from the shared primitives on every edit — proving the shared-lib reuse
// with zero network surface. The shared lib is consumed READ-ONLY (not forked).
//
// KEY: the current step is stored as a LOCATOR (kind + id), never as a question
// snapshot. The live Question is resolved from `survey.questions` by id on every
// render, so editing a headline/choice/setting flows into the preview instantly.
// ============================================================================
import { useEffect, useMemo, useReducer, useState } from "react";

import { validateAnswer } from "../../lib/answers";
import { applyBlockRandomization } from "../../lib/blocks";
import { defaultLanguageCode, resolveActiveLanguage, resolveI18n } from "../../lib/i18n";
import { nextStep, requiredQuestionIds } from "../../lib/logic";
import { QuestionRenderer } from "../../lib/questions/QuestionRenderer";
import { sanitizeHtml } from "../../lib/sanitizeHtml";
import {
  cardArrangementStyle,
  cardStyle,
  pageBackgroundStyle,
  primaryButtonStyle,
  questionTextStyle,
} from "../../lib/styling";
import type { AnswerMap, AnswerValue, NextStep, Styling, Survey } from "../../lib/types";

interface PreviewPaneProps {
  survey: Survey;
  /**
   * The authoring language to render in (the builder's selected language). When
   * omitted, falls back to the survey default — so callers that don't manage a
   * language switcher get the existing single-language behavior unchanged.
   */
  lang?: string;
}

/** A step LOCATOR — holds an id, never a question object (resolved live). */
type StepLoc =
  | { kind: "welcome" }
  | { kind: "question"; id: string }
  | { kind: "ending"; id: string }
  | { kind: "end" };

/** Strip empty answers before handing the map to the engine (mirrors the runtime). */
function cleanAnswers(map: AnswerMap): AnswerMap {
  const out: AnswerMap = {};
  for (const [k, v] of Object.entries(map)) {
    const empty =
      v === null ||
      v === undefined ||
      v === "" ||
      (Array.isArray(v) && v.length === 0) ||
      (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0);
    if (!empty) out[k] = v;
  }
  return out;
}

function firstLoc(survey: Survey, answers: AnswerMap): StepLoc {
  if (survey.welcomeCard?.enabled === true) return { kind: "welcome" };
  return toLoc(nextStep(survey, cleanAnswers(answers), null));
}

function toLoc(step: NextStep): StepLoc {
  if (step.type === "question") return { kind: "question", id: step.id };
  if (step.type === "ending") return { kind: "ending", id: step.id };
  return { kind: "end" };
}

export function PreviewPane({ survey, lang: langProp }: PreviewPaneProps) {
  // A "restart" nonce: bumping it resets the local answers + step. We also reset
  // whenever the survey STRUCTURE changes materially (question ids/order) so the
  // preview can't point at a step that no longer exists after an edit.
  const [restartNonce, restart] = useReducer((n: number) => n + 1, 0);
  const [answers, setAnswers] = useState<AnswerMap>({});
  const [trail, setTrail] = useState<string[]>([]);
  const [loc, setLoc] = useState<StepLoc>(() => firstLoc(survey, {}));
  const [error, setError] = useState<string | null>(null);

  // Render in the explicitly-selected authoring language when one is passed and it
  // resolves to a declared+enabled language; otherwise fall back to the survey
  // default (the existing single-language behavior).
  const lang = useMemo(
    () => resolveActiveLanguage(langProp, survey.languages),
    [langProp, survey.languages],
  );
  const defaultLang = useMemo(() => defaultLanguageCode(survey.languages), [survey.languages]);

  // The survey HANDED TO THE LOGIC ENGINE: the draft survey with each randomizing block's
  // members permuted into their flat slots (applyBlockRandomization). With no blocks (or all
  // randomize:"none") this is the SAME reference as `survey` — so the no-blocks preview is
  // byte-identical to before. The seed is STABLE while authoring (so the shuffle doesn't churn
  // on every keystroke) but re-rolls on Restart (restartNonce) so authors can preview a fresh
  // shuffle. Salting with the survey id keeps two surveys' previews independent.
  const previewSeed = `preview:${survey.id ?? "draft"}:${restartNonce}`;
  const flowSurvey = useMemo(
    () => applyBlockRandomization(survey, previewSeed),
    [survey, previewSeed],
  );

  // Structural signature: when the set/order of question ids changes (or the
  // restart button is pressed) we re-seat the preview at the first step. Editing
  // a question's TEXT/config does not reset — you keep your place while typing.
  // The welcome card's ENABLED flag and the set/order of ending IDS are structural
  // too: toggling the welcome card on/off changes the FIRST step, and adding/removing/
  // reordering endings changes which terminal step the flow lands on — so re-seat on
  // those. Editing welcome/ending TEXT (ids unchanged) does NOT reset (type-in-place).
  // Block membership/randomize is structural too: changing which questions a block groups, or
  // a block's randomize mode, changes the PRESENTED order — so re-seat (and re-shuffle) on it.
  const blockKey = (survey.blocks ?? [])
    .map((b) => `${b.id}:${b.randomize ?? "none"}:${b.questionIds.join(",")}`)
    .join(";");
  const structuralKey = useMemo(
    () =>
      survey.questions.map((q) => q.id).join("|") +
      `|w:${survey.welcomeCard?.enabled === true}` +
      `|e:${(survey.endings ?? []).map((e) => e.id).join(",")}` +
      `|b:${blockKey}` +
      `#${restartNonce}`,
    [survey.questions, survey.welcomeCard?.enabled, survey.endings, blockKey, restartNonce],
  );

  useEffect(() => {
    setAnswers({});
    setTrail([]);
    setError(null);
    setLoc(firstLoc(flowSurvey, {}));
    // Re-seat only when structure changes or on explicit restart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structuralKey]);

  const requiredIds = useMemo(
    () => requiredQuestionIds(flowSurvey, cleanAnswers(answers)),
    [flowSurvey, answers],
  );

  function setAnswer(questionId: string, value: AnswerValue | undefined) {
    setError(null);
    setAnswers((prev) => {
      const next = { ...prev };
      if (value === undefined) delete next[questionId];
      else next[questionId] = value;
      return next;
    });
  }

  function goNext() {
    if (loc.kind === "welcome") {
      setLoc(toLoc(nextStep(flowSurvey, cleanAnswers(answers), null)));
      return;
    }
    if (loc.kind !== "question") return;
    const q = flowSurvey.questions.find((x) => x.id === loc.id);
    if (!q) return;
    const isRequired = requiredIds.has(q.id);
    const err = validateAnswer(q, answers[q.id], isRequired);
    if (err) {
      setError(err);
      return;
    }
    setError(null);
    const cleaned = cleanAnswers(answers);
    // Navigate over the presentation-permuted survey so the preview reflects within-block
    // randomization (identity when no block randomizes — flat preview unchanged).
    const nxt = nextStep(flowSurvey, cleaned, q.id);
    setTrail((t) => [...t, q.id]);
    setLoc(toLoc(nxt));
  }

  function goBack() {
    if (trail.length === 0) return;
    setError(null);
    const prevId = trail[trail.length - 1];
    setTrail((t) => t.slice(0, -1));
    setLoc({ kind: "question", id: prevId });
  }

  // Resolve the LIVE question for the current locator (so edits show instantly).
  // If the locator points at an id that no longer exists (mid structural-reset
  // render), fall back to "end" rather than crashing.
  const activeQuestion =
    loc.kind === "question" ? survey.questions.find((x) => x.id === loc.id) ?? null : null;
  const canGoBack = trail.length > 0 && loc.kind === "question";

  // The same theming the public runtime applies (shared lib/styling) — so the
  // Style tab's edits show live here exactly as a respondent will see them.
  const styling = survey.styling ?? {};

  return (
    <div className="flex h-full flex-col" data-testid="preview-pane">
      <div className="flex items-center justify-between border-b border-hairline px-4 py-2.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">
          Live preview
        </span>
        <button
          type="button"
          onClick={restart}
          className="rounded-md px-2 py-1 text-xs font-medium text-brand hover:bg-brand/5"
        >
          Restart
        </button>
      </div>

      <div className="flex-1 overflow-auto bg-panel p-4" style={pageBackgroundStyle(styling)}>
        {survey.questions.length === 0 ? (
          <EmptyPreview />
        ) : (
          <div className="mx-auto w-full max-w-lg" style={questionTextStyle(styling)}>
            {styling.logoUrl && (
              <div className="mb-4 flex justify-center">
                <img src={styling.logoUrl} alt="" className="max-h-12 w-auto object-contain" />
              </div>
            )}
            <div
              className="rounded-2xl bg-white p-5 shadow-sm sm:p-6"
              style={{ ...cardStyle(styling), ...cardArrangementStyle(styling) }}
            >
              {loc.kind === "welcome" && (
                <WelcomeStep
                  survey={survey}
                  lang={lang}
                  defaultLang={defaultLang}
                  onNext={goNext}
                  styling={styling}
                />
              )}

              {loc.kind === "ending" && (
                <EndingStep survey={survey} endingId={loc.id} lang={lang} defaultLang={defaultLang} />
              )}

              {loc.kind === "end" && <EndStep />}

              {loc.kind === "question" &&
                (activeQuestion ? (
                  <div className="space-y-5">
                    <QuestionRenderer
                      question={activeQuestion}
                      value={answers[activeQuestion.id]}
                      onChange={(v) => setAnswer(activeQuestion.id, v)}
                      lang={lang}
                      defaultLang={defaultLang}
                      required={requiredIds.has(activeQuestion.id)}
                      answers={answers}
                      hiddenFields={survey.hiddenFields}
                    />
                    {error && (
                      <p role="alert" className="text-sm font-medium text-rose-600">
                        {error}
                      </p>
                    )}
                    <div className="flex items-center justify-between pt-1">
                      {canGoBack ? (
                        <button
                          type="button"
                          onClick={goBack}
                          className="rounded-lg px-3 py-2 text-sm font-medium text-muted hover:text-ink"
                        >
                          {resolveI18n(activeQuestion.backButtonLabel, lang, defaultLang) || "Back"}
                        </button>
                      ) : (
                        <span />
                      )}
                      <PreviewButton onClick={goNext} styling={styling}>
                        {resolveI18n(activeQuestion.buttonLabel, lang, defaultLang) || "Next"}
                      </PreviewButton>
                    </div>
                  </div>
                ) : (
                  <EndStep />
                ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Non-question steps                                                         //
// --------------------------------------------------------------------------- //
function WelcomeStep({
  survey,
  lang,
  defaultLang,
  onNext,
  styling,
}: {
  survey: Survey;
  lang: string;
  defaultLang: string;
  onNext: () => void;
  styling: Styling;
}) {
  const wc = survey.welcomeCard;
  const headline = resolveI18n(wc?.headline, lang, defaultLang);
  const html = resolveI18n(wc?.html, lang, defaultLang);
  const buttonLabel = resolveI18n(wc?.buttonLabel, lang, defaultLang) || "Start";
  return (
    <div className="space-y-5 text-center">
      {headline && <h1 className="text-xl font-bold text-ink font-display">{headline}</h1>}
      {html && (
        // Author-authored welcome HTML — sanitized (lib/sanitizeHtml) before render so the
        // builder preview matches the runtime's XSS boundary exactly.
        <div
          className="prose prose-sm mx-auto max-w-none text-muted"
          dangerouslySetInnerHTML={{ __html: sanitizeHtml(html) }}
        />
      )}
      <PreviewButton onClick={onNext} styling={styling}>
        {buttonLabel}
      </PreviewButton>
    </div>
  );
}

function EndingStep({
  survey,
  endingId,
  lang,
  defaultLang,
}: {
  survey: Survey;
  endingId: string;
  lang: string;
  defaultLang: string;
}) {
  const ending = (survey.endings ?? []).find((e) => e.id === endingId);
  const headline = resolveI18n(ending?.headline, lang, defaultLang) || "Thank you!";
  const subheader = resolveI18n(ending?.subheader, lang, defaultLang);
  return (
    <div className="space-y-2 py-6 text-center">
      <h1 className="text-xl font-bold text-ink font-display">{headline}</h1>
      {subheader && <p className="text-sm text-muted">{subheader}</p>}
    </div>
  );
}

function EndStep() {
  return (
    <div className="space-y-2 py-6 text-center">
      <h1 className="text-xl font-bold text-ink font-display">Thank you!</h1>
      <p className="text-sm text-muted">End of survey (preview).</p>
    </div>
  );
}

function PreviewButton({
  children,
  onClick,
  styling,
}: {
  children: React.ReactNode;
  onClick: () => void;
  styling: Styling;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center justify-center rounded-lg bg-brand px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-hover"
      style={primaryButtonStyle(styling)}
    >
      {children}
    </button>
  );
}

function EmptyPreview() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="rounded-xl border border-dashed border-hairline bg-white px-6 py-10 text-center text-sm text-muted">
        Add a question to see the live preview.
      </div>
    </div>
  );
}
