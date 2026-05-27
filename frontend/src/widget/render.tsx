// ============================================================================
// OpenSurvey widget — survey renderer.
//
// Mounts the EXISTING public runtime (islands/runtime SurveyRuntime + RuntimeApi)
// inside an injected, style-isolated SHADOW ROOT so the host page's CSS can't leak
// in and the survey's CSS can't leak out. The runtime loads the full survey
// definition itself (RuntimeApi.getSurvey → GET /public/surveys/<id>) and runs the
// normal response create/patch/finalize loop on submit — the widget does NOT
// reimplement any of that.
//
// Styling: the runtime uses Tailwind utility classes; inside shadow DOM they don't
// cascade from the host page, so we inject a curated CSS subset (styles.ts). Exact
// visual parity with the hosted runtime is DEFERRED (documented in README.md).
// ============================================================================
import { createRoot, type Root } from "react-dom/client";

import { RuntimeApi } from "../islands/runtime/api";
import { SurveyRuntime } from "../islands/runtime/SurveyRuntime";
import { WIDGET_CSS } from "./styles";
import type { WidgetConfig } from "./types";

/** A live, mounted survey instance the core can dismiss. */
export interface RenderedSurvey {
  surveyId: string;
  /** Tear down the React tree + remove the host element from the DOM. */
  dismiss(): void;
}

export interface RenderOptions {
  config: WidgetConfig;
  surveyId: string;
  /** Called when the survey is dismissed (close button) so the core can clean up. */
  onDismiss?: (surveyId: string) => void;
  /** Optional fetch override (tests inject this; RuntimeApi defaults to global fetch). */
  fetchImpl?: typeof fetch;
}

/**
 * Inject a shadow-DOM container, mount the runtime React tree into it, and return a
 * handle to dismiss it. `document` is assumed (browser/jsdom). Reuses RuntimeApi so
 * the tenant-prefixed public survey GET + response create/patch/finalize all run
 * exactly as on the hosted runtime.
 */
export function renderSurvey(opts: RenderOptions): RenderedSurvey {
  const { config, surveyId } = opts;

  const host = document.createElement("div");
  host.setAttribute("data-opensurvey-widget", surveyId);
  const shadow = host.attachShadow ? host.attachShadow({ mode: "open" }) : null;
  const mountTarget: ShadowRoot | HTMLElement = shadow ?? host;

  // Inject the scoped CSS into the shadow root (or host, if shadow DOM unavailable).
  const style = document.createElement("style");
  style.textContent = WIDGET_CSS;
  mountTarget.appendChild(style);

  // Card chrome: a wrapper + close button + the React mount node.
  const cardWrap = document.createElement("div");
  cardWrap.className = "cs-card-wrap";

  const closeBtn = document.createElement("button");
  closeBtn.className = "cs-close";
  closeBtn.setAttribute("aria-label", "Close survey");
  closeBtn.textContent = "×"; // ×

  const reactMount = document.createElement("div");
  reactMount.className = "cs-root";

  cardWrap.appendChild(closeBtn);
  cardWrap.appendChild(reactMount);
  mountTarget.appendChild(cardWrap);
  document.body.appendChild(host);

  // RuntimeApi builds `${apiBase}/${orgSlug}${path}`. To support a (possibly cross-origin)
  // data-api-host we fold the host into the base: `<apiHost><apiBase>` (apiHost is "" for the
  // same-origin default, leaving a bare "/api/v1" — identical to the runtime's own default).
  const api = new RuntimeApi({
    apiBase: `${config.apiHost}${config.apiBase}`,
    orgSlug: config.orgSlug,
    surveyId,
    fetchImpl: opts.fetchImpl,
  });

  let root: Root | null = createRoot(reactMount);
  const rendered: RenderedSurvey = {
    surveyId,
    dismiss() {
      if (root) {
        root.unmount();
        root = null;
      }
      host.remove();
    },
  };

  closeBtn.addEventListener("click", () => {
    rendered.dismiss();
    opts.onDismiss?.(surveyId);
  });

  root.render(<SurveyRuntime api={api} />);
  return rendered;
}
