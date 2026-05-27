// ============================================================================
// OpenSurvey embeddable widget — types.
//
// The widget consumes the EXISTING public widget APIs (apps/actions/public_*),
// which are tenant-prefixed (`/api/v1/<org_slug>/public/...`). It does NOT add or
// change any backend surface. Shapes here mirror what those endpoints return.
//
// IMPORTANT design note (read before touching trigger wiring):
//   The app-survey CATALOG (GET .../app-surveys) returns each survey's `triggers`
//   as a FLAT LIST OF ActionClass id-STRINGS (verified against the backend model +
//   tests: `Survey.triggers = ["<uuid>", ...]`). There is NO public endpoint that
//   exposes an ActionClass's name / type / config.urlMatch / config.cssSelector.
//   So the widget CANNOT do client-side urlMatch/cssSelector matching against the
//   server's action definitions — that data simply isn't reachable without a
//   backend change (which the task forbids).
//
//   Therefore every trigger funnels through `track(actionName)` and the SERVER
//   resolves the action (by config.key then name) and returns the surveys to fire:
//     - code:     host calls openSurvey.track("signed_up")
//     - pageview: the widget auto-fires track(<pageviewAction>) on load + SPA nav
//                 (action name configured via data-pageview-action, default
//                 "page_view"); an optional data-page-url-match restricts WHICH
//                 pages fire it (client-side substring/glob pre-filter only).
//     - click:    a delegated listener fires track(<clickAction>) when a click
//                 matches data-click-selector (host opts in via data-click-action
//                 + data-click-selector).
//   The catalog GET on init is session warm-up + the in-session de-dup baseline,
//   NOT the decision oracle (track is). See README.md "How triggers map".
// ============================================================================

/** One eligible app-survey as returned by BOTH widget endpoints (lightweight). */
export interface CatalogSurvey {
  id: string;
  name: string;
  survey_type: string;
  /** Raw ActionClass id-strings that trigger this survey (server-resolved on track). */
  triggers: string[];
  /** {displayLimit?: int, displayPercentage?: number} — server-enforced; informational here. */
  display_options: Record<string, unknown>;
}

/** GET .../app-surveys?userId=... response. */
export interface AppSurveysResponse {
  userId: string;
  person_id: string;
  surveys: CatalogSurvey[];
}

/** POST .../actions/track response. */
export interface TrackResponse {
  userId: string;
  person_id: string;
  action: string;
  matched_action: boolean;
  surveys: CatalogSurvey[];
}

/** POST .../app-surveys/<id>/displays response. */
export interface DisplayResponse {
  display_id: string;
  person_id: string;
}

/** Resolved widget configuration (from the <script> data-* attributes). */
export interface WidgetConfig {
  /** REQUIRED — the environment UUID (data-environment-id). */
  environmentId: string;
  /** REQUIRED — the org slug (data-org-slug); needed for the tenant URL prefix (ADR-001). */
  orgSlug: string;
  /** API host origin (data-api-host); defaults to the widget script's own origin. */
  apiHost: string;
  /** API path prefix (data-api-base); defaults to "/api/v1". */
  apiBase: string;
  /** Action name auto-tracked on pageview (data-pageview-action); default "page_view". */
  pageviewAction: string;
  /** Optional substring/glob a page URL must match before the pageview fires (data-page-url-match). */
  pageUrlMatch: string | null;
  /** Action name fired on a matching click (data-click-action); no default (opt-in). */
  clickAction: string | null;
  /** CSS selector a click must match to fire clickAction (data-click-selector). */
  clickSelector: string | null;
  /** Disable automatic pageview tracking (data-disable-pageview="true"). */
  disablePageview: boolean;
}

/** A trigger event the widget resolved client-side, funneled into a server track call. */
export type TriggerKind = "pageview" | "click" | "code";
