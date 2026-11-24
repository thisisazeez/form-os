// ============================================================================
// onForm — Builder Survey API client (authenticated admin CRUD).
//
// Wraps the AUTHENTICATED survey endpoints from contracts/openapi.yaml (Surveys
// tag), tenant-prefixed under /api/v1/{org_slug}/...:
//
//   GET    /surveys/{id}              -> Survey        (full definition; view_survey)
//   PUT    /surveys/{id}              -> Survey | 400  (replace; edit_survey)
//   POST   /surveys/{id}/status       -> Survey        (publish/pause/close)
//
// The status endpoint takes the body { "status": "<target>" } where target is one
// of "published" | "paused" | "closed" (see apps/surveys SurveyStatusUpdateSerializer
// + the frozen lifecycle matrix in services.py: draft→published, published→paused|
// closed, paused→published|closed, closed=terminal). It dispatches the rbac action on
// the TARGET status and ENFORCES the transition; an invalid edge (incl. a no-op to the
// same status, or any target of "draft") returns 400 {detail}. On success it returns
// the full Survey echo (carrying the new `status` + `published_at`). NOTE: the status
// endpoint does NOT re-validate the survey definition, so the builder client-gates
// publish with the shared validateSurvey before POSTing.
//
// Auth model: these run behind Django session auth (the admin shell), so unsafe
// methods need the CSRF token. We read the `csrftoken` cookie and send it as the
// `X-CSRFToken` header on PUT/POST. This is a no-op when the cookie is absent
// (e.g. in tests with a mocked fetch) and harmless under token auth.
// ============================================================================
import type { Survey, SurveyStatus } from "../../lib/types";

/**
 * The Survey echo the server returns from GET/PUT/POST-status. The shared `Survey`
 * type (../../lib/types — the FROZEN runtime/PublicSurvey shape) intentionally omits
 * the admin-only `status` + `published_at` columns, but the authenticated
 * SurveySerializer (apps/surveys/serializers.py) DOES include them. This local
 * extension types those fields without touching the shared contract.
 */
export type SavedSurvey = Survey & {
  status?: SurveyStatus;
  published_at?: string | null;
};

/**
 * A normalized survey API failure (save OR status transition). `messages` is a flat,
 * user-facing list assembled from the openapi ValidationErrorBody ({detail, errors,
 * schema_errors}) — the server runs survey-core's validateSurvey and returns jsonschema
 * messages in `schema_errors`, with logic-target/uniqueness errors surfacing under
 * `errors`; status-transition rejections come back as a single {detail} string.
 */
export class SaveError extends Error {
  constructor(
    public status: number,
    public messages: string[],
  ) {
    super(messages[0] ?? `Request failed (${status})`);
    this.name = "SaveError";
  }
}

/** Read a cookie value by name (returns "" if absent). */
export function readCookie(name: string): string {
  const match = (typeof document !== "undefined" ? document.cookie : "")
    .split("; ")
    .find((row) => row.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : "";
}

/**
 * Flatten an openapi ValidationErrorBody into display messages.
 *   - `schema_errors`: jsonschema messages (array of strings).
 *   - `errors`: DRF/object map — values may be string | string[] | nested; we
 *     join leaf strings, prefixing with the field path (e.g. "logic: ...").
 *   - `detail`: top-level message.
 */
export function flattenValidationBody(body: unknown): string[] {
  const out: string[] = [];
  if (!body || typeof body !== "object") return out;
  const b = body as Record<string, unknown>;

  if (Array.isArray(b.schema_errors)) {
    for (const m of b.schema_errors) if (typeof m === "string") out.push(m);
  }

  if (b.errors && typeof b.errors === "object") {
    walkErrors(b.errors as Record<string, unknown>, "", out);
  }

  if (typeof b.detail === "string" && out.length === 0) out.push(b.detail);
  return out;
}

function walkErrors(node: Record<string, unknown>, prefix: string, out: string[]): void {
  for (const [key, val] of Object.entries(node)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof val === "string") {
      out.push(`${path}: ${val}`);
    } else if (Array.isArray(val)) {
      for (const item of val) {
        if (typeof item === "string") out.push(`${path}: ${item}`);
        else if (item && typeof item === "object") walkErrors(item as Record<string, unknown>, path, out);
      }
    } else if (val && typeof val === "object") {
      walkErrors(val as Record<string, unknown>, path, out);
    }
  }
}

export interface BuilderApiConfig {
  /** Base API prefix; default "/api/v1". */
  apiBase?: string;
  orgSlug: string;
  surveyId: string;
  /** CSRF token; defaults to the `csrftoken` cookie. */
  csrfToken?: string;
  /** Optional fetch override (tests inject this). */
  fetchImpl?: typeof fetch;
}

export class BuilderApi {
  private base: string;
  private orgSlug: string;
  private surveyId: string;
  private csrfToken: string;
  private fetchImpl: typeof fetch;

  constructor(cfg: BuilderApiConfig) {
    this.base = (cfg.apiBase ?? "/api/v1").replace(/\/$/, "");
    this.orgSlug = cfg.orgSlug;
    this.surveyId = cfg.surveyId;
    this.csrfToken = cfg.csrfToken ?? readCookie("csrftoken");
    this.fetchImpl = cfg.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private url(path: string): string {
    return `${this.base}/${this.orgSlug}${path}`;
  }

  private headers(unsafe: boolean): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (unsafe && this.csrfToken) h["X-CSRFToken"] = this.csrfToken;
    return h;
  }

  /** GET the full survey definition (fallback when not injected on the host page). */
  async getSurvey(): Promise<SavedSurvey> {
    const res = await this.fetchImpl(this.url(`/surveys/${this.surveyId}`), {
      method: "GET",
      headers: this.headers(false),
      credentials: "same-origin",
    });
    if (!res.ok) {
      throw new SaveError(res.status, [`Could not load survey (${res.status}).`]);
    }
    return (await res.json()) as SavedSurvey;
  }

  /**
   * PUT the edited survey. Returns the saved Survey on 200. On 400 throws a
   * SaveError carrying the flattened schema/logic validation messages; other
   * non-OK statuses throw a SaveError with a generic message for that status.
   */
  async saveSurvey(body: Record<string, unknown>): Promise<SavedSurvey> {
    let res: Response;
    try {
      res = await this.fetchImpl(this.url(`/surveys/${this.surveyId}`), {
        method: "PUT",
        headers: this.headers(true),
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
    } catch {
      throw new SaveError(0, ["Network error — could not reach the server."]);
    }

    if (res.ok) {
      return (await res.json()) as SavedSurvey;
    }

    if (res.status === 400) {
      let parsed: unknown = null;
      try {
        parsed = await res.json();
      } catch {
        /* non-JSON body */
      }
      const messages = flattenValidationBody(parsed);
      throw new SaveError(400, messages.length ? messages : ["The survey could not be validated."]);
    }

    throw new SaveError(res.status, [genericMessage(res.status, "save")]);
  }

  /**
   * POST a status transition: `{ "status": target }` to /surveys/{id}/status.
   * Returns the server's full Survey echo (with the new `status` + `published_at`)
   * on 200. The server ENFORCES the lifecycle matrix and the per-target RBAC action,
   * so an invalid transition (incl. a no-op, or a forbidden caller) comes back as
   * 400/403 {detail}; we flatten that into a SaveError. The caller is responsible for
   * any client-side pre-gating (e.g. validating the survey before publishing).
   */
  async changeStatus(target: SurveyStatus): Promise<SavedSurvey> {
    let res: Response;
    try {
      res = await this.fetchImpl(this.url(`/surveys/${this.surveyId}/status`), {
        method: "POST",
        headers: this.headers(true),
        credentials: "same-origin",
        body: JSON.stringify({ status: target }),
      });
    } catch {
      throw new SaveError(0, ["Network error — could not reach the server."]);
    }

    if (res.ok) {
      return (await res.json()) as SavedSurvey;
    }

    if (res.status === 400 || res.status === 403) {
      let parsed: unknown = null;
      try {
        parsed = await res.json();
      } catch {
        /* non-JSON body */
      }
      const messages = flattenValidationBody(parsed);
      throw new SaveError(
        res.status,
        messages.length ? messages : [genericMessage(res.status, "publish")],
      );
    }

    throw new SaveError(res.status, [genericMessage(res.status, "publish")]);
  }

  /** Convenience: transition the survey to `published` (draft|paused → published). */
  publishSurvey(): Promise<SavedSurvey> {
    return this.changeStatus("published");
  }

  /** Convenience: transition the survey to `closed` (published|paused → closed). */
  closeSurvey(): Promise<SavedSurvey> {
    return this.changeStatus("closed");
  }

  /** Convenience: transition the survey to `paused` (published → paused). */
  pauseSurvey(): Promise<SavedSurvey> {
    return this.changeStatus("paused");
  }
}

function genericMessage(status: number, op: "save" | "publish" = "save"): string {
  if (status === 403) {
    return op === "publish"
      ? "You don't have permission to change this survey's status."
      : "You don't have permission to edit this survey.";
  }
  if (status === 404) return "Survey not found.";
  if (status >= 500) return "Server error — please try again.";
  return op === "publish" ? `Could not update status (${status}).` : `Save failed (${status}).`;
}
