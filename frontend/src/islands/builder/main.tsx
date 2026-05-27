// ============================================================================
// OpenSurvey — Survey Builder island (ENTRYPOINT).
//
// The admin authoring UI (behind auth), mounted into the Django admin shell via
// django-vite. Hydrates from the survey definition injected by the host page,
// CRUDs the questions/settings, shows a LIVE PREVIEW (reusing the shared runtime
// lib), and SAVEs via the authenticated Survey CRUD API.
//
// MOUNT CONTRACT (pre-agreed with builder-backend — the host page mounts this):
//
//   <div id="opensurvey-builder"
//        data-survey-id="<uuid>"               REQUIRED
//        data-org-slug="<org_slug>"            REQUIRED (tenant path prefix, ADR-001)
//        data-api-base="/api/v1"               OPTIONAL (default "/api/v1")
//        data-initial-survey='<Survey JSON>'   the survey definition, EITHER:
//                                                (a) raw JSON inline, OR
//                                                (b) the id of a <script type="application/json">
//                                                    payload (Django {{ x|json_script:"id" }}).
//        data-config-id="opensurvey-builder-config"  OPTIONAL explicit json_script id
//                                                (mirrors the runtime host's data-config-id).
//   ></div>
//
// Resolution order for the initial survey:
//   1. data-config-id   → read that json_script element's textContent.
//   2. data-initial-survey: if it parses as JSON → use it; else treat its value
//      as a json_script element id and read that element's textContent.
//   3. Neither present/parseable → GET /surveys/{id} via the authenticated API.
//
// If backend has not shipped the host page yet, THIS comment is the contract.
// ============================================================================
import { createRoot } from "react-dom/client";

import type { Survey } from "../../lib/types";
import { BuilderApi } from "./api";
import { BuilderApp } from "./BuilderApp";
// The snake→camel server-survey bridge lives in surveyModel (shared with BuilderApp's Save echo,
// which must apply the SAME normalization or follow-ups/welcome-card vanish on Save). Re-exported
// below so existing importers (tests) keep resolving it from ./main.
import { normalizeInitialSurvey } from "./surveyModel";
import type { ActionClass } from "./types";

export { normalizeInitialSurvey };

/** Read + parse a <script type="application/json"> payload by element id. */
function readJsonScript<T>(id: string): T | null {
  const node = document.getElementById(id);
  if (!node || !node.textContent) return null;
  try {
    return JSON.parse(node.textContent) as T;
  } catch {
    return null;
  }
}

/**
 * Resolve the env's ActionClasses injected on the mount node (data-action-classes, an
 * ActionClassSerializer array). Used by the Settings tab's app-survey trigger picker. Returns []
 * when absent or unparseable (the picker then shows the "create an action first" hint) — the
 * builder never depends on these being present, so a link survey is unaffected.
 */
export function resolveActionClasses(el: HTMLElement): ActionClass[] {
  const raw = el.dataset.actionClasses;
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? (parsed as ActionClass[]) : [];
  } catch {
    return [];
  }
}

/**
 * Resolve the injected survey from the mount node's dataset, supporting BOTH the
 * raw-JSON form and the json_script-id form (see the mount contract above).
 * Returns null when nothing usable is injected (caller falls back to a GET).
 */
export function resolveInitialSurvey(el: HTMLElement): Survey | null {
  // (1) explicit json_script id
  const configId = el.dataset.configId;
  if (configId) {
    const fromScript = readJsonScript<Survey>(configId);
    if (fromScript) return fromScript;
  }

  // (2) data-initial-survey: raw JSON, or an element id pointing at a json_script
  const raw = el.dataset.initialSurvey;
  if (raw) {
    const trimmed = raw.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        return JSON.parse(trimmed) as Survey;
      } catch {
        /* fall through to id resolution */
      }
    }
    const fromScript = readJsonScript<Survey>(trimmed);
    if (fromScript) return fromScript;
  }

  return null;
}


export async function mountBuilder(el: HTMLElement): Promise<void> {
  const surveyId = el.dataset.surveyId;
  const orgSlug = el.dataset.orgSlug;

  if (!surveyId || !orgSlug) {
    el.textContent = "Survey builder: missing data-survey-id / data-org-slug.";
    return;
  }

  const apiBase = el.dataset.apiBase ?? "/api/v1";
  const api = new BuilderApi({ apiBase, orgSlug, surveyId });

  // Hydrate from the injected definition, else fetch it.
  let initialSurvey = resolveInitialSurvey(el);
  if (!initialSurvey) {
    try {
      initialSurvey = await api.getSurvey();
    } catch {
      el.textContent = "Survey builder: could not load the survey definition.";
      return;
    }
  }

  // Defensive defaults + snake→camel normalization so the builder always has a
  // renderable shape and an existing survey's hidden fields survive the round-trip.
  const survey: Survey = normalizeInitialSurvey(initialSurvey);

  // The env's ActionClasses (host-injected) — the Settings tab's app-survey trigger picker.
  const actionClasses = resolveActionClasses(el);

  createRoot(el).render(
    <BuilderApp
      api={api}
      initialSurvey={survey}
      orgSlug={orgSlug}
      actionClasses={actionClasses}
    />,
  );
}

const el = document.getElementById("opensurvey-builder");
if (el) {
  void mountBuilder(el);
}
