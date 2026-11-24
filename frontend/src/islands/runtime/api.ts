// ============================================================================
// onForm — Public Survey API client (runtime island).
//
// Wraps the four public endpoints from contracts/openapi.yaml. Unauthenticated;
// rate-limited (handles 429). All paths are tenant-prefixed: /api/v1/{org_slug}/...
//
//   GET    /public/surveys/{id}[?link_token]              -> PublicSurvey
//   POST   /public/surveys/{id}/responses                 -> PublicResponseHandle
//   PATCH  /public/responses/{id}?edit_token              -> PublicResponseHandle
//   POST   /public/responses/{id}/finalize?edit_token     -> PublicResponseHandle
// ============================================================================
import type { AnswerMap, PublicResponseHandle, Survey } from "../../lib/types";

/** Categorized API failure the UI maps to a user-facing end state. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public kind:
      | "not_found" // 404 — survey not published / missing
      | "gone" // 410 — single-use consumed / survey closed
      | "rate_limited" // 429
      | "forbidden" // 403 — bad/expired edit token
      | "validation" // 400
      | "network" // fetch threw / no response
      | "server", // 5xx / other
    public retryAfter?: number,
  ) {
    super(`API ${status} (${kind})`);
    this.name = "ApiError";
  }
}

function classify(status: number): ApiError["kind"] {
  if (status === 404) return "not_found";
  if (status === 410) return "gone";
  if (status === 429) return "rate_limited";
  if (status === 403) return "forbidden";
  if (status === 400) return "validation";
  if (status >= 500) return "server";
  return "server";
}

export interface RuntimeApiConfig {
  /** Base API prefix; default "/api/v1". */
  apiBase?: string;
  orgSlug: string;
  surveyId: string;
  /** Optional fetch override (tests inject this). */
  fetchImpl?: typeof fetch;
}

export class RuntimeApi {
  private base: string;
  private orgSlug: string;
  private surveyId: string;
  private fetchImpl: typeof fetch;

  constructor(cfg: RuntimeApiConfig) {
    this.base = (cfg.apiBase ?? "/api/v1").replace(/\/$/, "");
    this.orgSlug = cfg.orgSlug;
    this.surveyId = cfg.surveyId;
    this.fetchImpl = cfg.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private url(path: string): string {
    return `${this.base}/${this.orgSlug}${path}`;
  }

  private async request<T>(input: string, init: RequestInit): Promise<T> {
    let res: Response;
    try {
      res = await this.fetchImpl(input, {
        ...init,
        headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
      });
    } catch {
      throw new ApiError(0, "network");
    }
    if (!res.ok) {
      const retryAfter = Number(res.headers.get("Retry-After")) || undefined;
      throw new ApiError(res.status, classify(res.status), retryAfter);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  /** GET the published survey definition for the runtime. */
  async getSurvey(linkToken?: string | null): Promise<Survey> {
    const q = linkToken ? `?link_token=${encodeURIComponent(linkToken)}` : "";
    return this.request<Survey>(this.url(`/public/surveys/${this.surveyId}${q}`), {
      method: "GET",
    });
  }

  /** POST a new (partial) response. Returns the response id + edit token. */
  async createResponse(
    data: AnswerMap,
    language: string,
    linkToken?: string | null,
    meta?: Record<string, unknown>,
  ): Promise<PublicResponseHandle> {
    const body: Record<string, unknown> = { data, language };
    if (linkToken) body.link_token = linkToken;
    if (meta) body.meta = meta;
    return this.request<PublicResponseHandle>(this.url(`/public/surveys/${this.surveyId}/responses`), {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  /** PATCH partial answers as the respondent progresses (merged per question key). */
  async patchResponse(
    responseId: string,
    editToken: string,
    data: AnswerMap,
    language?: string,
  ): Promise<PublicResponseHandle> {
    const body: Record<string, unknown> = { data };
    if (language) body.language = language;
    return this.request<PublicResponseHandle>(
      this.url(`/public/responses/${responseId}?edit_token=${encodeURIComponent(editToken)}`),
      { method: "PATCH", body: JSON.stringify(body) },
    );
  }

  /** POST finalize — mark the response finished (validates required answers server-side). */
  async finalizeResponse(responseId: string, editToken: string): Promise<PublicResponseHandle> {
    return this.request<PublicResponseHandle>(
      this.url(`/public/responses/${responseId}/finalize?edit_token=${encodeURIComponent(editToken)}`),
      { method: "POST", body: JSON.stringify({}) },
    );
  }
}
