"""
TENANT URL configuration (ROOT_URLCONF).

Active when TenantSlugMiddleware (apps/common/middleware.py) has resolved an Organization
from the org slug in the URL and activated its schema (ADR-001 path-based routing).

The middleware strips ONLY the slug, preserving any API prefix in front of it. So the org
slug is gone by the time these patterns match, but the `/api/v1/` prefix (when present) is
NOT — it maps to openapi.yaml's `/api/v1/{org_slug}/...` shape after the slug is removed:
  request `/api/v1/acme/org`  →  middleware → pattern `api/v1/org`
  request `/acme/s/<id>`        →  middleware → pattern `s/<id>`

Wave 2 mounts the tenant API + HTMX admin + public survey runtime here, e.g.:
  - path("api/v1/", include("apps.<context>.urls"))        # builder/admin API (RBAC) →
        serves openapi.yaml `/api/v1/{org_slug}/...` (slug already stripped by middleware)
  - path("api/v1/public/", include("apps.responses.public_urls"))  # rate-limited runtime
  - path("s/<uuid:survey_id>", runtime_view)               # public HTML runtime island
        → serves `/{org_slug}/s/{survey_id}`

For the Phase-0 skeleton we expose a tenant-scoped health/landing so a resolved org
renders a page proving routing works end-to-end.
"""
from django.urls import include, path

from apps.common import views as common_views
from apps.organizations.urls import htmx_urlpatterns as org_htmx_urlpatterns
from apps.responses import public_views as responses_public_views

# A1 surveys-admin imports (wired HTMX admin surface). Kept in ruff's sorted order alongside
# the existing imports above so `ruff check` stays clean; the merge-critical fence is placed
# around the ROUTE additions in `urlpatterns` below (begin/end markers).
from apps.responses.urls_htmx import urlpatterns as responses_htmx_urlpatterns
from apps.surveys import builder_views as surveys_builder_views
from apps.surveys.urls_htmx import urlpatterns as surveys_htmx_urlpatterns
from apps.surveys.views_htmx import dashboard_home as a1_dashboard_home

urlpatterns = [
    # === A1 surveys-admin (begin) ===
    # Tenant home (``/<org_slug>/``) → the real wired dashboard (replaces the island-smoke
    # tenant_home). Only A1 owns this route.
    path("", a1_dashboard_home, name="tenant_home"),
    # === A1 surveys-admin (end) ===
    path("healthz", common_views.healthz, name="tenant_healthz"),
    # Wave 0F (foundation): TEMPORARY smoke fixture exercising the authenticated admin shell
    # (templates/_layouts/app_shell.html). `/<org_slug>/__shell_smoke` → after slug strip →
    # `__shell_smoke`. login_required. DELETE with apps.common.views.app_shell_smoke once a real
    # admin page extends the shell (see tests/test_app_shell.py).
    path("__shell_smoke", common_views.app_shell_smoke, name="app_shell_smoke"),
    # Global search (top-bar): `/<org_slug>/search?q=` → after slug strip → `search`.
    # Searches surveys (by name) + contacts (by attribute value), workspace/PII scoped.
    path("search", common_views.search_page, name="search"),
    # Proves the API-family routing `/api/v1/<org_slug>/...` (openapi.yaml) resolves the
    # tenant: after the middleware strips the slug, `/api/v1/<slug>/healthz` arrives here
    # as `api/v1/healthz`.
    path("api/v1/healthz", common_views.healthz, name="tenant_api_healthz"),
    # Wave 2a (Phase 1): org-scoped settings / members / invitations (public-schema models,
    # but addressed per-org via the routed tenant — slug already stripped by the middleware,
    # so `/api/v1/<slug>/org` arrives as `api/v1/org`). Org-level RBAC is enforced in the
    # views' permission classes (apps.organizations.permissions), NOT can() (Wave 2b).
    path("api/v1/", include("apps.organizations.urls")),
    # Wave 2b (Phase 2): tenant workspace + teams API (RBAC-gated via apps.common.permissions
    # .can()). Mounted under `api/v1/` so `/api/v1/<slug>/workspaces` arrives as
    # `api/v1/workspaces` after the middleware strips the slug (openapi Workspaces + Teams).
    path("api/v1/", include("apps.workspaces.urls")),
    path("api/v1/", include("apps.teams.urls")),
    # Wave 2c (survey-core): tenant Survey CRUD + lifecycle API and the authenticated Response
    # READ API (RBAC-gated via apps.common.permissions.can()). Mounted under `api/v1/` so e.g.
    # `/api/v1/<slug>/environments/<id>/surveys` and `/api/v1/<slug>/surveys/<id>/responses`
    # arrive here after the middleware strips the slug (openapi Surveys + Responses tags).
    # The public response WRITE/submission path is the runtime wave — NOT mounted here.
    path("api/v1/", include("apps.surveys.urls")),
    path("api/v1/", include("apps.responses.urls")),
    # === WS-11 app-survey actions (begin) ===
    # ActionClass management API (RBAC-gated via apps.common.permissions.can()). Mounted under
    # `api/v1/` so `/api/v1/<slug>/environments/<env_id>/actions` arrives here as
    # `api/v1/environments/<env_id>/actions` after the middleware strips the slug.
    path("api/v1/", include("apps.actions.urls")),
    # === WS-11 app-survey actions (end) ===
    # Wave 2c (contacts-segments): tenant contacts + segments API (RBAC-gated via
    # apps.common.permissions.can()). Mounted under `api/v1/` (slug stripped by middleware →
    # openapi Contacts + Segments). Contacts = typed-attribute CRUD + dedup-by-email; Segments
    # = CRUD + filter evaluation (apps.segments.filters).
    path("api/v1/", include("apps.contacts.urls")),
    path("api/v1/", include("apps.segments.urls")),
    # Wave 3a (runtime-backend): the PUBLIC survey-taking path (openapi "Public Survey" tag).
    # Unauthenticated + rate-limited. Mounted under `api/v1/` so `/api/v1/<slug>/public/...`
    # arrives here as `api/v1/public/...` after the middleware strips the slug:
    #   GET   api/v1/public/surveys/<id>            → PublicSurvey definition (surveys app)
    #   POST  api/v1/public/surveys/<id>/responses  → create response (+ edit_token; ADR-009)
    #   PATCH api/v1/public/responses/<id>          → save-as-you-go
    #   POST  api/v1/public/responses/<id>/finalize → finish + consume single-use link
    #   POST  api/v1/public/surveys/<id>/displays   → impression tracking (funnel)
    # The presigned upload-url endpoint is DEFERRED (a follow-on) — not routed.
    path("api/v1/", include("apps.surveys.public_urls")),
    path("api/v1/", include("apps.responses.public_urls")),
    # === WS-11 app-survey widget (begin) ===
    # The PUBLIC widget APIs (unauthenticated + rate-limited). Mounted under `api/v1/` so
    # `/api/v1/<slug>/public/environments/<env>/actions/track` + `.../app-surveys` arrive here
    # as `api/v1/public/environments/<env>/...` after the middleware strips the slug:
    #   POST  api/v1/public/environments/<env>/actions/track → track action + return app surveys
    #   GET   api/v1/public/environments/<env>/app-surveys    → eligible app surveys for a userId
    # The widget reuses the existing public survey-definition GET + response create/patch/
    # finalize/displays endpoints (above) for rendering, impressions, and submission.
    path("api/v1/", include("apps.actions.public_urls")),
    # === WS-11 app-survey widget (end) ===
    # Wave 3a: the PUBLIC HTML runtime host page (tenant HTML family, ADR-001):
    #   `/<org_slug>/s/<survey_id>` → after slug strip → `s/<survey_id>`. Renders the minimal
    # template that mounts the React runtime island (django-vite) for a PUBLISHED survey.
    path("s/<uuid:survey_id>", responses_public_views.public_runtime_host, name="public_runtime"),
    # Wave 3b (builder-backend): the AUTHENTICATED survey-builder HTML host page (tenant HTML
    # family, ADR-001): `/<org_slug>/surveys/<survey_id>/edit` → after slug strip →
    # `surveys/<survey_id>/edit`. login_required + RBAC-gated (can(edit_survey)); renders the
    # minimal template that mounts the React builder island (django-vite) with the survey's
    # full definition injected for offline hydration. Distinct from the `api/v1/...` Survey
    # CRUD (that is the JSON API the island calls; this is just the host page).
    path(
        "surveys/<uuid:survey_id>/edit",
        surveys_builder_views.survey_builder_host,
        name="survey_builder",
    ),
    # HTMX members page (server-rendered admin), tenant-scoped: `/<org_slug>/members`.
    path("", include((org_htmx_urlpatterns, "organizations"), namespace="organizations_htmx")),
    # Tenant survey/response/contacts/segments/team/settings HTMX admin (server-rendered;
    # NOT the DRF API which mounts under api/v1/ above). Slug already stripped by the middleware.
    # === A1 surveys-admin (begin) ===
    # Wired survey + response HTMX admin pages:
    #   /<org_slug>/surveys                       surveys list (+ ?new=1; POST create draft)
    #   /<org_slug>/surveys/<id>/share            survey share
    #   /<org_slug>/surveys/<id>/results          results / analytics
    #   /<org_slug>/surveys/<id>/responses        responses table
    #   /<org_slug>/surveys/<id>/responses/<rid>  response detail
    # Listed AFTER the builder host so the literal `surveys/<id>/edit` keeps priority; these
    # add the sibling `surveys`, `.../share`, `.../results`, `.../responses[...]` patterns.
    path("", include((surveys_htmx_urlpatterns, "surveys"), namespace="surveys_htmx")),
    path("", include((responses_htmx_urlpatterns, "responses"), namespace="responses_htmx")),
    # === A1 surveys-admin (end) ===
    # === A2 contacts-segments (begin) ===
    # `/<org_slug>/contacts` arrives as `contacts`, `/<org_slug>/segments/new` as `segments/new`.
    # Wired to the SAME services/models the API uses; RBAC via apps.common.permissions.can().
    path("", include("apps.contacts.urls_htmx")),
    path("", include("apps.segments.urls_htmx")),
    # === A2 contacts-segments (end) ===
    # === A3 team-settings (begin) ===
    #   /<org_slug>/teams                          → teams_htmx        (Teams & access matrix)
    #   /<org_slug>/settings/workspace[/...]        → workspaces_htmx   (Workspace settings)
    #   /<org_slug>/settings  /settings/billing     → org_settings_htmx (Org settings & billing)
    #   /<org_slug>/account                         → org_settings_htmx (Account settings)
    # RBAC via apps.common.permissions.can() + org-role checks (mirrors members_page).
    path("", include("apps.teams.urls_htmx")),
    path("", include("apps.workspaces.urls_htmx")),
    path("", include("apps.organizations.urls_settings")),
    # === A3 team-settings (end) ===
    # === PLATFORM (apikeys/webhooks/audit) begin ===
    # Three platform Settings tabs (server-rendered HTMX), tenant-scoped under /<org_slug>/:
    #   /<org_slug>/settings/api-keys   → apikeys_htmx  (list/create/revoke API keys; secret shown once)
    #   /<org_slug>/settings/webhooks   → webhooks_htmx (create/edit/delete webhook endpoints)
    #   /<org_slug>/settings/audit-log  → audit_htmx    (paginated, filterable audit log; owner/admin)
    # All three are owner/admin-gated via apps.common.permissions.can(). The DRF x-api-key
    # authenticator (apps.apikeys.authentication) is wired in REST_FRAMEWORK, not here.
    path("", include("apps.apikeys.urls_settings")),
    path("", include("apps.webhooks.urls_settings")),
    path("", include("apps.audit.urls_settings")),
    # === PLATFORM (apikeys/webhooks/audit) end ===

    # === INTEGRATIONS (WS-10) begin ===
    #   /<org_slug>/settings/integrations → integrations_htmx (Slack connect/disconnect hub;
    #   surfaces the existing Webhooks tab by link; lists deferred integrations read-only).
    #   Owner/admin-gated via apps.common.permissions.can("manage_integration").
    path("", include("apps.integrations.urls_settings")),
    # === INTEGRATIONS (WS-10) end ===

    # === WS-11 app-survey settings (begin) ===
    # Two app-survey Settings tabs (server-rendered form-POST, like webhooks), tenant-scoped:
    #   /<org_slug>/settings/actions     → actions_htmx (list/create/edit/delete ActionClasses)
    #   /<org_slug>/settings/connection  → actions_htmx (env id + widget embed snippet; read-only)
    # Workspace-scoped RBAC via apps.common.permissions.can() (view/create/edit/delete_action_class),
    # NOT org owner/admin — actions are survey-trigger config scoped to a workspace environment.
    path("", include("apps.actions.urls_settings")),
    # === WS-11 app-survey settings (end) ===
]
