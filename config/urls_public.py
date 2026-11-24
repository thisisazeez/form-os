"""
PUBLIC-schema URL configuration (PUBLIC_SCHEMA_URLCONF).

Active when the request resolves to the `public` schema — i.e. NO `/<org_slug>/` prefix.
Serves the marketing/landing page, the health check, the Django admin, and (Wave 2,
Phase 1) the non-tenant auth endpoints (`/api/v1/auth/...` — signup/verify/login/reset),
which live in the public schema per contracts/openapi.yaml.

Tenant-scoped URLs (`/<org_slug>/...`) are handled by config/urls.py (ROOT_URLCONF).
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.common import views as common_views
from apps.organizations.urls import public_htmx_urlpatterns as org_public_htmx_urlpatterns
from apps.organizations.urls import public_urlpatterns as org_public_urlpatterns

urlpatterns = [
    # `/` is the public marketing homepage. The old anonymous→login / authed→dashboard
    # dispatcher still lives at `/app` (name="landing" is kept for existing reverse() callers).
    path("", common_views.marketing_landing, name="landing"),
    path("landing", common_views.marketing_landing, name="marketing_landing"),
    path("app", common_views.landing, name="app_home"),
    path("healthz", common_views.healthz, name="healthz"),
    path("django-admin/", admin.site.urls),
    # ---------------------------------------------------------------------
    # Wave 2a (Phase 1) — public-schema auth + org bootstrap.
    #
    # Everything here sits under the `auth/` (or `api/v1/auth/`) prefix because
    # TenantSlugMiddleware (apps/common/middleware.py — OUTSIDE this producer's territory)
    # treats only `auth` (and a few infra names) as PUBLIC_CANDIDATES for the FIRST path
    # segment; any other first segment is taken as an org slug and 404s. The frozen openapi
    # paths `/api/v1/me` and `/api/v1/invitations/accept` are therefore shipped under
    # `/api/v1/auth/...`, and the HTMX pages under `/auth/...`. See the BLOCKING openapi
    # deviation note in SUMMARY + FOLLOWUPS.md — the proper fix is the middleware producer
    # widening PUBLIC_CANDIDATES (+ RESERVED_SLUGS) so these can move to their contract paths.
    # ---------------------------------------------------------------------
    # JSON API (DRF):
    #   /api/v1/auth/signup|verify-email|login|logout|password-reset[/confirm]
    #   /api/v1/auth/me                       (openapi path: /api/v1/me)
    path("api/v1/", include("apps.accounts.urls")),
    #   /api/v1/auth/organizations            (org creation; not in openapi)
    #   /api/v1/auth/invitations/accept       (openapi path: /api/v1/invitations/accept)
    path("api/v1/", include((org_public_urlpatterns, "organizations"), namespace="org_public")),
    # HTMX (server-rendered) auth pages:
    #   /auth/signup|login|logout|verify-email|reset-password
    path("auth/", include("apps.accounts.urls_htmx")),
    #   /auth/organizations/new   /auth/invitations/accept   (org bootstrap pages)
    path(
        "",
        include((org_public_htmx_urlpatterns, "organizations"), namespace="org_public_htmx"),
    ),
]

# Serve uploaded media (account avatars) from the local filesystem in dev. /media/ resolves to
# the public schema (PUBLIC_CANDIDATES in TenantSlugMiddleware), so it belongs in THIS urlconf.
# In production a web server / object storage fronts MEDIA_URL instead.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
