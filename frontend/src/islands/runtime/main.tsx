// ============================================================================
// OpenSurvey — Public Survey Runtime island (ENTRYPOINT).
//
// The ONLY public JS surface (PLAN.md §8). Renders the survey question-by-question
// using the shared logic engine for next-step/branching + required-gating; partial
// save (PATCH) as the respondent progresses; final submit (finalize). Endpoints are
// unauthenticated + rate-limited (openapi.yaml Public Survey).
//
// HOST-PAGE CONTRACT (runtime-backend owns the template; data injected on the mount
// node #opensurvey-runtime as data-* attributes):
//
//   <div id="opensurvey-runtime"
//        data-survey-id="<uuid>"               REQUIRED
//        data-org-slug="<org_slug>"            REQUIRED (tenant path prefix, ADR-001)
//        data-link-token="<token>"             OPTIONAL (required if survey.single_use)
//        data-api-base="/api/v1"               OPTIONAL (default "/api/v1")
//        data-language="en"                    OPTIONAL (requested response language)
//        data-initial-survey='<PublicSurvey>'  OPTIONAL (pre-injected JSON; skips GET)
//        data-prefill='{"utm_source":"x"}'     OPTIONAL (hidden-field prefills from URL/link)
//        data-remove-branding="true|false"     OPTIONAL (workspace white-label; hides footer)
//   ></div>
//
// Falls back to reading ?lang= and ?<token>= from the URL if not injected.
// ============================================================================
import { createRoot } from "react-dom/client";

import type { AnswerMap, Survey } from "../../lib/types";
import { RuntimeApi } from "./api";
import { SurveyRuntime } from "./SurveyRuntime";

function parseJsonAttr<T>(raw: string | undefined): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function mountRuntime(el: HTMLElement): void {
  const surveyId = el.dataset.surveyId;
  const orgSlug = el.dataset.orgSlug;

  if (!surveyId || !orgSlug) {
    // Without the survey/org binding there is nothing to render. Leave a hint.
    el.textContent = "Survey runtime: missing data-survey-id / data-org-slug.";
    return;
  }

  const params = new URLSearchParams(window.location.search);
  const linkToken = el.dataset.linkToken ?? params.get("link_token") ?? params.get("token") ?? null;
  const requestedLanguage = el.dataset.language ?? params.get("lang") ?? null;
  const apiBase = el.dataset.apiBase ?? "/api/v1";
  const initialSurvey = parseJsonAttr<Survey>(el.dataset.initialSurvey);
  const prefill = parseJsonAttr<AnswerMap>(el.dataset.prefill) ?? undefined;
  // Workspace white-label flag (enterprise): when "true", hide the "Powered by OpenSurvey"
  // footer on the terminal screens. Anything other than the exact string "true" → false.
  const removeBranding = el.dataset.removeBranding === "true";

  const api = new RuntimeApi({ apiBase, orgSlug, surveyId });

  createRoot(el).render(
    <SurveyRuntime
      api={api}
      linkToken={linkToken}
      initialSurvey={initialSurvey}
      requestedLanguage={requestedLanguage}
      prefill={prefill}
      removeBranding={removeBranding}
    />,
  );
}

const el = document.getElementById("opensurvey-runtime");
if (el) {
  mountRuntime(el);
}
