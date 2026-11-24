// ============================================================================
// onForm embeddable widget — ENTRYPOINT (built to a single self-contained
// IIFE bundle at /static/widget/opensurvey-widget.js with React bundled in).
//
// Embed contract (matches the admin settings page):
//   <script src="<APP_ORIGIN>/static/widget/opensurvey-widget.js"
//           data-environment-id="<ENV_ID>"
//           data-org-slug="<ORG_SLUG>"></script>
//
// On load this reads its own <script> data-*, constructs the runtime, and auto-inits.
// It also exposes `window.openSurvey` with { init, setUserId, track, reset } so the
// host app can identify users and fire "code" triggers. Re-running the script is a
// no-op (singleton): the existing instance is preserved.
// ============================================================================
import { OnFormWidget } from "./core";
import { findWidgetScript, resolveConfig } from "./config";

/** The public surface attached to window.openSurvey. */
export interface OpenSurveyPublicApi {
  /** Re-run init (idempotent). Normally auto-called; exposed for manual SPA boots. */
  init(): void;
  /** Identify the host's user; persists the id for subsequent track/display calls. */
  setUserId(id: string): void;
  /** Fire a named "code" action; the server returns any surveys to show. */
  track(action: string, props?: Record<string, unknown>): void;
  /** Forget the current user + in-session de-dup (a new visitor). */
  reset(): void;
  /** True once the widget has resolved its config and booted. */
  readonly ready: boolean;
}

declare global {
  interface Window {
    openSurvey?: OpenSurveyPublicApi;
  }
}

/** Build the public API object backed by an OnFormWidget instance (or a no-op shell). */
function buildPublicApi(instance: OnFormWidget | null): OpenSurveyPublicApi {
  return {
    init() {
      void instance?.init();
    },
    setUserId(id: string) {
      instance?.setUserId(id);
    },
    track(action: string, props?: Record<string, unknown>) {
      void instance?.track(action, props);
    },
    reset() {
      instance?.reset();
    },
    get ready() {
      return instance !== null;
    },
  };
}

/** Boot the widget from its <script> tag. Idempotent (singleton on window.openSurvey). */
export function boot(): OpenSurveyPublicApi {
  if (window.openSurvey && window.openSurvey.ready) {
    return window.openSurvey;
  }

  const script = findWidgetScript();
  const config = script ? resolveConfig(script) : null;
  const instance = config ? new OnFormWidget(config) : null;

  const api = buildPublicApi(instance);
  window.openSurvey = api;

  if (instance) {
    void instance.init();
  }
  return api;
}

// Auto-boot at load. boot() ALWAYS sets window.openSurvey (so manual init() is possible even
// if the widget <script> isn't found yet), and auto-runs init() when it resolves a valid config
// from the page's widget <script>. It is idempotent (singleton), so this is safe to run on every
// load — including the IIFE bundle on a real page and a (mis)use via `type="module"`, where the
// host can still call window.openSurvey.init() after staging the tag.
//
// In unit tests, the OTHER widget specs import core/storage/etc. directly (never main), so they
// never trip this. main.test.ts imports { boot } and resets window.openSurvey in beforeEach;
// at import time findWidgetScript() finds no staged tag, so this sets a harmless no-op shell that
// the test immediately replaces.
if (typeof document !== "undefined" && typeof window !== "undefined") {
  boot();
}
