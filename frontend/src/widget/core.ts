// ============================================================================
// onForm widget — runtime core (`window.openSurvey`).
//
// Public API (task contract):
//   init()                         auto-run from the <script> tag (reads data-*)
//   setUserId(id)                  identify the host's user (persisted)
//   track(actionName, props?)      the "code" trigger — funnel an action to the server
//   reset()                        clear userId + in-session de-dup (new visitor)
//
// On init: persist/generate a userId, fetch the app-survey catalog (warm-up + the
// in-session de-dup baseline), and wire the three triggers:
//   - pageview: track(<pageviewAction>) on load + SPA history pushState/replaceState
//               + popstate; optional client-side urlMatch pre-filter (data-page-url-match).
//   - click:    a single delegated listener; on a click matching data-click-selector,
//               track(<clickAction>).
//   - code:     the .track() API itself.
//
// When a trigger fires → POST track → for each returned survey NOT already shown this
// session → record a display (POST .../displays) → render it (reuse the runtime).
// The SERVER enforces gating (displayLimit / recontact cooldown / displayPercentage);
// the widget adds the cheap in-session de-dup the task asks for.
//
// WHY track-funneled (not client-side urlMatch/cssSelector against server actions):
// the catalog exposes triggers only as raw ActionClass id-strings; no public endpoint
// exposes an action's name/type/urlMatch/cssSelector, so the server (which CAN resolve
// by name/key) is the matching oracle. See types.ts + README.md.
// ============================================================================
import { WidgetApi } from "./api";
import { renderSurvey, type RenderedSurvey } from "./render";
import { WidgetStorage } from "./storage";
import type { CatalogSurvey, TrackResponse, WidgetConfig } from "./types";

export interface OpenSurveyDeps {
  /** Test seam: override fetch (passed to both the widget API and the runtime). */
  fetchImpl?: typeof fetch;
  /** Test seam: inject a storage instance (defaults to real Web Storage). */
  storage?: WidgetStorage;
  /** Test seam: override how a survey is rendered (defaults to renderSurvey). */
  render?: typeof renderSurvey;
}

export class OnFormWidget {
  readonly config: WidgetConfig;
  private api: WidgetApi;
  private storage: WidgetStorage;
  private renderImpl: typeof renderSurvey;
  private fetchImpl?: typeof fetch;

  private userId: string;
  private initialized = false;
  private live = new Map<string, RenderedSurvey>();
  private cleanups: Array<() => void> = [];

  constructor(config: WidgetConfig, deps: OpenSurveyDeps = {}) {
    this.config = config;
    this.fetchImpl = deps.fetchImpl;
    this.api = new WidgetApi({
      apiHost: config.apiHost,
      apiBase: config.apiBase,
      orgSlug: config.orgSlug,
      environmentId: config.environmentId,
      fetchImpl: deps.fetchImpl,
    });
    this.storage = deps.storage ?? new WidgetStorage();
    this.renderImpl = deps.render ?? renderSurvey;
    this.userId = this.storage.ensureUserId();
  }

  /** Auto-run boot: load the catalog, wire triggers, fire the initial pageview. */
  async init(): Promise<void> {
    if (this.initialized) return;
    this.initialized = true;

    // Warm-up catalog: surfaces any surveys the server already considers eligible and
    // seeds the de-dup baseline. Failures are non-fatal (the widget still wires triggers).
    try {
      await this.api.getAppSurveys(this.userId);
    } catch {
      /* warm-up is best-effort */
    }

    this.wirePageview();
    this.wireClick();

    // Initial pageview (unless disabled). SPA navigations are caught by the history hooks.
    if (!this.config.disablePageview) {
      void this.firePageview();
    }
  }

  /** Identify the host's user. Persists the id and uses it for all subsequent calls. */
  setUserId(id: string): void {
    if (!id) return;
    this.userId = id;
    this.storage.setUserId(id);
  }

  /** The "code" trigger: funnel a named action to the server and show any returned surveys. */
  async track(action: string, props?: Record<string, unknown>): Promise<void> {
    if (!action) return;
    await this.fire(action, props);
  }

  /** Forget the current user + in-session de-dup state (a brand new visitor). */
  reset(): void {
    // Dismiss any live surveys and clear the live map so the NEW visitor can be shown them
    // again. The live map mirrors the storage de-dup set (cleared below) and also guards
    // against a double-render within a single fire() pass; both must reset together.
    for (const s of this.live.values()) s.dismiss();
    this.live.clear();
    this.storage.reset();
    this.userId = this.storage.ensureUserId();
  }

  /** Remove all listeners + dismiss any live surveys (used by tests / teardown). */
  destroy(): void {
    for (const off of this.cleanups.splice(0)) off();
    for (const s of this.live.values()) s.dismiss();
    this.live.clear();
  }

  // ----------------------------------------------------------------------- //
  //  trigger wiring
  // ----------------------------------------------------------------------- //
  private wirePageview(): void {
    if (this.config.disablePageview) return;
    // SPA navigations: pushState/replaceState don't emit events, so wrap them, plus popstate.
    const onNav = () => void this.firePageview();

    const hist = window.history;
    const origPush = hist.pushState.bind(hist);
    const origReplace = hist.replaceState.bind(hist);
    hist.pushState = function (this: History, ...args: Parameters<History["pushState"]>) {
      const r = origPush(...args);
      onNav();
      return r;
    } as History["pushState"];
    hist.replaceState = function (this: History, ...args: Parameters<History["replaceState"]>) {
      const r = origReplace(...args);
      onNav();
      return r;
    } as History["replaceState"];
    window.addEventListener("popstate", onNav);

    this.cleanups.push(() => {
      hist.pushState = origPush;
      hist.replaceState = origReplace;
      window.removeEventListener("popstate", onNav);
    });
  }

  private wireClick(): void {
    const { clickAction, clickSelector } = this.config;
    if (!clickAction || !clickSelector) return;
    const handler = (ev: Event) => {
      const target = ev.target as Element | null;
      if (!target || typeof target.closest !== "function") return;
      if (target.closest(clickSelector)) {
        void this.fire(clickAction);
      }
    };
    document.addEventListener("click", handler, true);
    this.cleanups.push(() => document.removeEventListener("click", handler, true));
  }

  /** Fire the configured pageview action, honoring the optional client-side url pre-filter. */
  private async firePageview(): Promise<void> {
    if (this.config.disablePageview) return;
    const url = currentUrl();
    if (this.config.pageUrlMatch && !urlMatches(url, this.config.pageUrlMatch)) return;
    await this.fire(this.config.pageviewAction, undefined, url);
  }

  // ----------------------------------------------------------------------- //
  //  the common funnel: track → de-dup → display → render
  // ----------------------------------------------------------------------- //
  private async fire(action: string, props?: Record<string, unknown>, url?: string): Promise<void> {
    let res: TrackResponse;
    try {
      res = await this.api.track(this.userId, action, props, url ?? currentUrl());
    } catch {
      return; // never let a tracking failure break the host page
    }
    for (const survey of res.surveys) {
      await this.maybeShow(survey);
    }
  }

  /** Show one survey iff not already shown this session; record its display first. */
  private async maybeShow(survey: CatalogSurvey): Promise<void> {
    if (this.storage.hasShown(survey.id)) return;
    if (this.live.has(survey.id)) return;
    // Mark immediately so a second trigger in the same tick can't double-render.
    this.storage.markShown(survey.id);

    try {
      await this.api.recordDisplay(survey.id, this.userId);
    } catch {
      // If the impression POST fails the server didn't count it; still render so the
      // user sees the survey (the de-dup mark already prevents a re-show this session).
    }

    const rendered = this.renderImpl({
      config: this.config,
      surveyId: survey.id,
      fetchImpl: this.fetchImpl,
      onDismiss: (id) => this.live.delete(id),
    });
    this.live.set(survey.id, rendered);
  }
}

/** The current page URL (guarded for non-browser/test contexts). */
function currentUrl(): string {
  try {
    return window.location.href;
  } catch {
    return "";
  }
}

/**
 * Client-side pageview pre-filter. Supports a simple glob (`*` = any run) OR plain
 * substring match — intentionally lenient (it only GATES the track call; the server
 * remains the authority on whether a survey actually fires).
 */
export function urlMatches(url: string, pattern: string): boolean {
  if (!pattern) return true;
  if (pattern.includes("*")) {
    const re = new RegExp(
      "^" + pattern.split("*").map(escapeRegExp).join(".*") + "$",
    );
    return re.test(url);
  }
  return url.includes(pattern);
}

function escapeRegExp(s: string): string {
  return s.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
}
