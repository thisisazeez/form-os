// ============================================================================
// OpenSurvey — public Survey Runtime component.
//
// Ties the flow state machine (useSurveyFlow) to the question renderers and the
// welcome/ending/error screens. Clean, functional UI (Tailwind). Accessibility
// basics: labelled inputs, focus moves to each new question, the primary action
// is keyboard-reachable. Full a11y polish is DEFERRED.
//
// STYLING NOTE: this island renders inside templates/base.html, whose inline
// Tailwind CDN config registers the OpenSurvey BRAND TOKENS — `brand`(#0d9488),
// `brand-hover`, `secondary`, `success`(#8ac449), `ink`, `muted`, `surface`
// (#faf8f7), `panel`, `hairline`, `font-display`(Poppins), `font-body`(Inter) —
// and loads Material Symbols. So we use those token utilities directly (NOT raw
// `slate-*`/`indigo`/hex), keeping the public ending states on-brand + brandable.
//
// LAYOUT NOTE: the welcome card / questions render inside a white card; the
// terminal ENDING + ERROR (closed/expired/invalid) states are full-bleed on the
// warm `bg-surface`, matching designs/stitch/{survey-completed,link-expired}.png.
// ============================================================================
import { useEffect, useRef } from "react";

import { resolveI18n } from "../../lib/i18n";
import { sanitizeHtml } from "../../lib/sanitizeHtml";
import {
  cardArrangementStyle,
  cardStyle,
  ctaButtonStyle,
  pageBackgroundStyle,
  primaryButtonStyle,
  progressBarColor,
  questionTextStyle,
} from "../../lib/styling";
import type { Ending, Styling, Survey } from "../../lib/types";
import { QuestionRenderer } from "../../lib/questions/QuestionRenderer";
import { ApiError } from "./api";
import type { RuntimeApi } from "./api";
import { useSurveyFlow } from "./useSurveyFlow";
import type { AnswerMap } from "../../lib/types";

export interface SurveyRuntimeProps {
  api: RuntimeApi;
  linkToken?: string | null;
  initialSurvey?: Survey | null;
  requestedLanguage?: string | null;
  prefill?: AnswerMap;
  /**
   * Workspace white-label flag (enterprise). When true, the public runtime HIDES the
   * "Powered by OpenSurvey" footer on the terminal (ending / closed) screens. Threaded from the
   * runtime host page (data-remove-branding) via main.tsx. Default false (branding shown).
   */
  removeBranding?: boolean;
}

export function SurveyRuntime(props: SurveyRuntimeProps) {
  const flow = useSurveyFlow({
    api: props.api,
    linkToken: props.linkToken,
    initialSurvey: props.initialSurvey,
    requestedLanguage: props.requestedLanguage,
    prefill: props.prefill,
  });

  const styling = flow.survey?.styling ?? {};
  const showBranding = props.removeBranding !== true;
  const { phase } = flow;

  // Terminal states render FULL-BLEED (no app chrome, no white card) — a centered
  // hero on warm `bg-surface` with a "Powered by OpenSurvey" footer (unless white-labeled).
  if (phase.kind === "error") {
    return (
      <TerminalShell>
        <ClosedScreen error={phase.error} showBranding={showBranding} />
      </TerminalShell>
    );
  }

  if (phase.kind === "ending") {
    const ending = (flow.survey?.endings ?? []).find((e) => e.id === phase.endingId);
    return (
      <TerminalShell>
        <CompletedScreen ending={ending} lang={flow.lang} defaultLang={flow.defaultLang} styling={styling} showBranding={showBranding} />
      </TerminalShell>
    );
  }

  if (phase.kind === "end") {
    return (
      <TerminalShell>
        <CompletedScreen ending={undefined} lang={flow.lang} defaultLang={flow.defaultLang} styling={styling} showBranding={showBranding} />
      </TerminalShell>
    );
  }

  // QUOTAS (additive): the server marked this response over-quota and the quota declared no
  // ending (or one that no longer resolves) — show the built-in 'quota full' screen. An
  // over-quota response WITH a resolvable ending is shown via the `{kind:"ending"}` branch above.
  if (phase.kind === "quotaFull") {
    return (
      <TerminalShell>
        <QuotaFullScreen />
      </TerminalShell>
    );
  }

  // In-progress: welcome card + questions, inside a white card with progress.
  // The PAGE background (color/image) themes a min-h-screen wrapper; an optional
  // logo sits above the card. The card itself carries roundness + card-bg.
  return (
    <div className="flex min-h-screen w-full justify-center" style={pageBackgroundStyle(styling)}>
      <div className="w-full max-w-2xl px-4 py-8 sm:py-12" style={questionTextStyle(styling)}>
        {styling.logoUrl && <SurveyLogo url={styling.logoUrl} />}
        {!styling.hideProgressBar && phase.kind !== "loading" && (
          <ProgressBar value={flow.progress} color={progressBarColor(styling)} />
        )}
        <div
          className="rounded-2xl border border-hairline bg-white p-6 shadow-sm sm:p-8"
          style={{ ...cardStyle(styling), ...cardArrangementStyle(styling) }}
        >
          <CardBody flow={flow} styling={styling} />
        </div>
      </div>
    </div>
  );
}

/** Optional survey logo, centered above the card (public runtime + preview). */
function SurveyLogo({ url }: { url: string }) {
  return (
    <div className="mb-5 flex justify-center">
      <img src={url} alt="" className="max-h-16 w-auto object-contain" />
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  In-card body (loading / welcome / question)                               //
// --------------------------------------------------------------------------- //
function CardBody({
  flow,
  styling,
}: {
  flow: ReturnType<typeof useSurveyFlow>;
  styling: Styling;
}) {
  const { phase } = flow;

  switch (phase.kind) {
    case "loading":
      return <Centered>Loading…</Centered>;

    case "welcome": {
      const wc = flow.survey?.welcomeCard;
      const headline = resolveI18n(wc?.headline, flow.lang, flow.defaultLang);
      const html = resolveI18n(wc?.html, flow.lang, flow.defaultLang);
      const buttonLabel =
        resolveI18n(wc?.buttonLabel, flow.lang, flow.defaultLang) || "Start";
      return (
        <div className="space-y-6 text-center">
          {headline && <h1 className="font-display text-2xl font-bold tracking-tight text-ink">{headline}</h1>}
          {html && (
            // Author-authored welcome HTML — sanitized (lib/sanitizeHtml) before render. The
            // stored HTML is author-controlled, so this is a hard XSS boundary, not cosmetic.
            <div
              className="mx-auto max-w-none text-muted"
              dangerouslySetInnerHTML={{ __html: sanitizeHtml(html) }}
            />
          )}
          <PrimaryButton styling={styling} onClick={flow.startSurvey}>
            {buttonLabel}
          </PrimaryButton>
        </div>
      );
    }

    case "question":
      return <QuestionStep flow={flow} styling={styling} question={phase.question} />;

    default:
      // ending / end / error are rendered full-bleed at the SurveyRuntime level.
      return null;
  }
}

// --------------------------------------------------------------------------- //
//  Question step (renderer + nav)                                             //
// --------------------------------------------------------------------------- //
function QuestionStep({
  flow,
  styling,
  question: q,
}: {
  flow: ReturnType<typeof useSurveyFlow>;
  styling: Styling;
  question: import("../../lib/types").Question;
}) {
  const required = flow.requiredIds.has(q.id);
  const headingRef = useRef<HTMLDivElement>(null);

  // Move focus to the question on each new step (a11y basic).
  useEffect(() => {
    headingRef.current?.focus();
  }, [q.id]);

  const nextLabel = resolveI18n(q.buttonLabel, flow.lang, flow.defaultLang) || "Next";
  const backLabel = resolveI18n(q.backButtonLabel, flow.lang, flow.defaultLang) || "Back";

  return (
    <div className="space-y-6">
      <div ref={headingRef} tabIndex={-1} className="outline-none">
        <QuestionRenderer
          question={q}
          value={flow.answers[q.id]}
          onChange={(v) => flow.setAnswer(q.id, v)}
          lang={flow.lang}
          defaultLang={flow.defaultLang}
          required={required}
          answers={flow.recallAnswers}
          hiddenFields={flow.survey?.hiddenFields}
        />
      </div>

      {flow.validationError && (
        <p role="alert" className="text-sm font-medium text-red-600">
          {flow.validationError}
        </p>
      )}

      <div className="flex items-center justify-between pt-2">
        {flow.canGoBack ? (
          <button
            type="button"
            onClick={flow.back}
            disabled={flow.saving}
            className="rounded-lg px-4 py-2 text-sm font-medium text-muted transition-colors hover:text-ink disabled:opacity-50"
          >
            {backLabel}
          </button>
        ) : (
          <span />
        )}
        <PrimaryButton styling={styling} onClick={flow.next} disabled={flow.saving}>
          {flow.saving ? "Saving…" : nextLabel}
        </PrimaryButton>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Terminal: survey COMPLETED (thank-you)  — designs/stitch/survey-completed  //
// --------------------------------------------------------------------------- //
function CompletedScreen({
  ending,
  lang,
  defaultLang,
  styling,
  showBranding = true,
}: {
  ending: Ending | undefined;
  lang: string;
  defaultLang: string;
  styling: Styling;
  showBranding?: boolean;
}) {
  // Redirect endings: navigate the browser to the configured URL (no card render).
  useEffect(() => {
    if (ending?.type === "redirect" && ending.redirectUrl) {
      window.location.href = ending.redirectUrl;
    }
  }, [ending]);

  // While a redirect ending is bouncing the browser, don't flash the thank-you.
  if (ending?.type === "redirect" && ending.redirectUrl) {
    return <Centered>Redirecting…</Centered>;
  }

  // Use the survey's configured ending content if present, else the warm default.
  const headline = resolveI18n(ending?.headline, lang, defaultLang) || "Thanks for your feedback! 🎉";
  const subheader =
    resolveI18n(ending?.subheader, lang, defaultLang) ||
    "Your response helps us improve. We truly appreciate you taking the time to share your thoughts with our team.";
  const buttonLabel = resolveI18n(ending?.buttonLabel, lang, defaultLang);
  const buttonLink = ending?.buttonLink;

  return (
    <div className="flex w-full max-w-md flex-col items-center px-2 text-center">
      {/* Success check — soft halo behind a SOLID green badge with a white tick.
         Inline SVG (not the icon font): the base.html Material Symbols instance is
         pinned to FILL=0, so a filled glyph isn't available; SVG renders the design's
         filled disc deterministically and offline-safe. */}
      <div className="mb-8 flex h-28 w-28 items-center justify-center rounded-full bg-success/15">
        <SuccessBadge />
      </div>

      <h1 className="font-display text-3xl font-bold tracking-tight text-ink sm:text-4xl">{headline}</h1>
      {subheader && (
        <p className="mt-4 max-w-sm text-base leading-relaxed text-muted">{subheader}</p>
      )}

      {/* Optional secondary CTA — only when the survey configures one. */}
      {buttonLabel && buttonLink && (
        <a
          href={buttonLink}
          className="mt-10 inline-flex w-full max-w-xs items-center justify-center gap-2 rounded-lg bg-brand px-6 py-3.5 text-base font-medium text-white shadow-sm transition hover:bg-brand-hover hover:-translate-y-0.5 hover:opacity-95"
          style={ctaButtonStyle(styling)}
        >
          {buttonLabel}
          <ArrowForward />
        </a>
      )}

      {showBranding && <PoweredByFooter className="mt-16" />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Terminal: survey CLOSED / link expired / invalid — designs/stitch/link-expired
// --------------------------------------------------------------------------- //
function ClosedScreen({ error, showBranding = true }: { error: ApiError; showBranding?: boolean }) {
  const { title, body } = closedCopy(error);
  return (
    <div className="flex w-full max-w-md flex-col items-center">
      <div
        role="status"
        className="flex w-full flex-col items-center rounded-2xl border border-hairline bg-white p-8 text-center shadow-lg sm:p-10"
      >
        {/* Calm neutral status icon (muted outlined circle-check) — NOT alarming red.
           Inline SVG keeps it crisp regardless of the icon-font load. */}
        <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-panel">
          <CircleCheckOutline />
        </div>
        <h1 className="font-display text-2xl font-bold tracking-tight text-ink sm:text-3xl">{title}</h1>
        <p className="mt-3 max-w-[280px] text-base leading-relaxed text-muted">{body}</p>
      </div>

      {showBranding && <PoweredByFooter className="mt-8" />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Terminal: QUOTA FULL (built-in screen — over-quota with no survey ending)   //
// --------------------------------------------------------------------------- //
/**
 * The built-in 'quota full' screen shown when the server marked the respondent over-quota and the
 * quota declared no `endingId` (or one that no longer resolves). Calm + on-brand, reusing the
 * closed-screen card idiom — the response WAS recorded (finished); we just couldn't accept it
 * toward the (now-full) quota's segment.
 */
function QuotaFullScreen() {
  return (
    <div className="flex w-full max-w-md flex-col items-center">
      <div
        role="status"
        className="flex w-full flex-col items-center rounded-2xl border border-hairline bg-white p-8 text-center shadow-lg sm:p-10"
      >
        <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-panel">
          <CircleCheckOutline />
        </div>
        <h1 className="font-display text-2xl font-bold tracking-tight text-ink sm:text-3xl">
          We’ve reached our response limit
        </h1>
        <p className="mt-3 max-w-[300px] text-base leading-relaxed text-muted">
          Thanks so much for your interest — we’ve already collected enough responses for this
          group. We truly appreciate you taking the time.
        </p>
      </div>

      <PoweredByFooter className="mt-8" />
    </div>
  );
}

/**
 * Calm, non-alarming copy for the closed/invalid terminal state. Every reachable
 * in-island failure maps here (the host view 404s a non-PUBLISHED survey before
 * the island mounts — that path is server-rendered and out of the island's reach):
 *   - not_found (404): survey missing / not published.
 *   - gone     (410): survey auto-closed (date/cap) OR single-use link consumed/expired/required.
 *   - forbidden(403): expired edit token (mid-flow) — reframed calmly, not as an error.
 *   - rate_limited / validation / network / server: generic calm "try again later".
 */
function closedCopy(error: ApiError): { title: string; body: string } {
  switch (error.kind) {
    case "not_found":
      return {
        title: "This survey is closed",
        body: "Thanks for your interest — this survey isn’t available or is no longer accepting responses.",
      };
    case "gone":
      return {
        title: "This survey is closed",
        body: "Thanks for your interest — responses are no longer being collected.",
      };
    case "forbidden":
      return {
        title: "This survey is closed",
        body: "Your session has ended. Please reopen the survey link to start again.",
      };
    case "rate_limited":
      return {
        title: "Just a moment",
        body: error.retryAfter
          ? `We’re receiving a lot of responses right now. Please try again in ${error.retryAfter}s.`
          : "We’re receiving a lot of responses right now. Please try again in a little while.",
      };
    default:
      return {
        title: "This survey is unavailable",
        body: "Something went wrong on our side. Please check your connection and try again in a little while.",
      };
  }
}

// --------------------------------------------------------------------------- //
//  Shared terminal UI                                                         //
// --------------------------------------------------------------------------- //
/** Full-bleed centered wrapper for the terminal states (no app chrome). */
function TerminalShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-[80vh] w-full flex-col items-center justify-center px-6 py-12">
      {children}
    </div>
  );
}

// --- Inline SVG icons (font-independent; color comes from `currentColor` so the
//     brand token text-* class drives the hue — no raw hex in markup). --- //

/** Solid green success badge with a white tick (thank-you screen). */
function SuccessBadge() {
  return (
    <svg
      className="h-16 w-16 text-success"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="11" fill="currentColor" />
      <path
        d="M7.5 12.4l3 3 6-6.4"
        fill="none"
        stroke="white"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Muted outlined circle-check status icon (closed / expired / invalid screen). */
function CircleCheckOutline() {
  return (
    <svg
      className="h-10 w-10 text-muted"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M8.5 12.2l2.4 2.4 4.6-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Small forward arrow for the optional thank-you CTA. */
function ArrowForward() {
  return (
    <svg
      className="h-[18px] w-[18px]"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M5 12h14m-6-6l6 6-6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Subtle "Powered by OpenSurvey" footer (brand-accented wordmark). */
function PoweredByFooter({ className = "" }: { className?: string }) {
  return (
    <p className={`font-display text-sm font-semibold text-muted ${className}`}>
      Powered by <span className="text-brand">OpenSurvey</span>
    </p>
  );
}

function ProgressBar({ value, color }: { value: number; color?: string }) {
  return (
    <div
      className="mb-4 h-1.5 w-full overflow-hidden rounded-full bg-panel"
      role="progressbar"
      aria-valuenow={Math.round(value * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full rounded-full bg-brand transition-all duration-300"
        style={{ width: `${Math.round(value * 100)}%`, ...(color ? { backgroundColor: color } : {}) }}
      />
    </div>
  );
}

function PrimaryButton({
  children,
  onClick,
  disabled,
  styling,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  styling: Styling;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center justify-center rounded-lg bg-brand px-6 py-2.5 text-base font-medium text-white shadow-sm transition hover:bg-brand-hover hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
      style={primaryButtonStyle(styling)}
    >
      {children}
    </button>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="py-10 text-center text-muted">{children}</div>;
}
