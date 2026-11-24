// ============================================================================
// onForm widget — public widget API client.
//
// Wraps the THREE public widget endpoints (apps/actions/public_*). All paths are
// tenant-prefixed `<apiHost>/api/v1/<orgSlug>/public/...` because TenantSlugMiddleware
// resolves the org schema from the slug in the path (ADR-001) — there is no env-only
// public route, hence the widget MUST know the org slug (data-org-slug).
//
//   GET  .../environments/<env>/app-surveys?userId=...        -> AppSurveysResponse
//   POST .../environments/<env>/actions/track                  -> TrackResponse
//   POST .../environments/<env>/app-surveys/<survey>/displays  -> DisplayResponse
//
// Submission (response create/patch/finalize) is NOT done here — the widget reuses
// the EXISTING runtime island (RuntimeApi + SurveyRuntime) for rendering + submission.
// ============================================================================
import type {
  AppSurveysResponse,
  DisplayResponse,
  TrackResponse,
  WidgetConfig,
} from "./types";

export interface WidgetApiConfig {
  apiHost: string;
  apiBase: string;
  orgSlug: string;
  environmentId: string;
  /** Optional fetch override (tests inject this). */
  fetchImpl?: typeof fetch;
}

export class WidgetApi {
  private apiHost: string;
  private apiBase: string;
  private orgSlug: string;
  private environmentId: string;
  private fetchImpl: typeof fetch;

  constructor(cfg: WidgetApiConfig) {
    this.apiHost = cfg.apiHost.replace(/\/$/, "");
    this.apiBase = (cfg.apiBase || "/api/v1").replace(/\/$/, "");
    this.orgSlug = cfg.orgSlug;
    this.environmentId = cfg.environmentId;
    this.fetchImpl = cfg.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  /** Build a tenant-prefixed absolute URL: <host>/<base>/<orgSlug>/public<path>. */
  private url(path: string): string {
    return `${this.apiHost}${this.apiBase}/${this.orgSlug}/public/environments/${this.environmentId}${path}`;
  }

  private async request<T>(input: string, init: RequestInit): Promise<T> {
    const res = await this.fetchImpl(input, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
    if (!res.ok) {
      throw new Error(`onForm widget API ${res.status} for ${input}`);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  /** GET the app-survey catalog for this user (init warm-up + de-dup baseline). */
  async getAppSurveys(userId: string): Promise<AppSurveysResponse> {
    const q = `?userId=${encodeURIComponent(userId)}`;
    return this.request<AppSurveysResponse>(this.url(`/app-surveys${q}`), { method: "GET" });
  }

  /** POST a tracked action; the server returns the surveys to fire now. */
  async track(
    userId: string,
    action: string,
    attributes?: Record<string, unknown>,
    url?: string,
  ): Promise<TrackResponse> {
    const body: Record<string, unknown> = { userId, action };
    if (attributes && Object.keys(attributes).length > 0) body.attributes = attributes;
    if (url) body.url = url;
    return this.request<TrackResponse>(this.url(`/actions/track`), {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  /** POST an impression for a survey the widget actually showed (per-shown survey). */
  async recordDisplay(surveyId: string, userId: string): Promise<DisplayResponse> {
    return this.request<DisplayResponse>(this.url(`/app-surveys/${surveyId}/displays`), {
      method: "POST",
      body: JSON.stringify({ userId }),
    });
  }
}

/** Build a WidgetApi from a resolved WidgetConfig (+ optional fetch override for tests). */
export function makeWidgetApi(config: WidgetConfig, fetchImpl?: typeof fetch): WidgetApi {
  return new WidgetApi({
    apiHost: config.apiHost,
    apiBase: config.apiBase,
    orgSlug: config.orgSlug,
    environmentId: config.environmentId,
    fetchImpl,
  });
}
