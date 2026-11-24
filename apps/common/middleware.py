"""
Path-based tenant routing middleware (ADR-001).

contracts/decisions.md ADR-001 + contracts/openapi.yaml specify path-based routing on a
shared domain where the ORG SLUG appears in the URL path. There are TWO URL families the
slug can sit in, and this middleware resolves both:

  1. HTML/admin + public runtime:  `/<org_slug>/...`        (e.g. `/acme/`, `/acme/s/<id>`)
  2. JSON API (openapi.yaml):        `/api/v1/<org_slug>/...` (e.g. `/api/v1/acme/org`)

…while these non-tenant paths stay on the PUBLIC schema:
  - `/`, `/healthz`, `/django-admin/...`, `/static/...`, `/media/...`, `/favicon.ico`
  - `/api/v1/auth/...` (signup/verify/login/reset live in the public schema — openapi.yaml)

WHY A CUSTOM MIDDLEWARE (not django-tenants' TenantSubfolderMiddleware):
The built-in (a) REQUIRES a non-empty `TENANT_SUBFOLDER_PREFIX` (raises ImproperlyConfigured
otherwise), forcing URLs like `/orgs/<slug>/...` that contradict the frozen `/<org_slug>/...`
shape in openapi.yaml; and (b) resolves the subfolder against the **Domain** table
(`domain=<subfolder>`), not `Organization.slug` — the opposite of ADR-001. The Phase-0 brief
explicitly permits "a documented custom middleware." This is it.

BEHAVIOR (mirrors django-tenants semantics where it matters):
  - Always start at the public schema (tenant metadata lives there).
  - Compute the tenant-candidate segment:
      * path starts with `/api/v1/` → candidate is the segment AFTER `api/v1`
        (so `auth` there means the public auth API; any other value is an org slug).
      * otherwise → candidate is the first path segment.
  - Candidate empty or in the public set → stay public, use PUBLIC_SCHEMA_URLCONF, and set
    `request.tenant` to the public tenant (parity with django-tenants so downstream code can
    read `request.tenant` unconditionally).
  - Else look the candidate up in `Organization.slug`:
      * Hit  → activate that org's schema, STRIP the slug from `request.path_info` so the
               tenant ROOT_URLCONF matches the remainder (the API prefix `/api/v1` is kept
               in front so tenant API urlpatterns mount at `api/v1/...`), use ROOT_URLCONF.
      * Miss → 404 (no tenant-existence oracle; mirrors django-tenants no_tenant_found).
  - Authz runs AFTER this (rbac.md §10): a user with no Membership in the routed org is
    denied at IsOrgMember (403) — that is Wave 2.

The `Domain` model is retained (models.md §1.3) so a future switch to subdomain routing
needs no data migration — only swapping this middleware back to host-based resolution.

NOTE for Wave 2: `Organization.slug`'s reserved-slug validator runs on full_clean()/forms,
NOT on `Organization.objects.create()`. The org-creation path (Phase 1) MUST call
full_clean() (or otherwise enforce RESERVED_SLUGS) so a tenant can't claim e.g. `api`/`auth`.
"""
from __future__ import annotations

from django.db import connection
from django.http import Http404
from django.urls import set_urlconf
from django.utils.deprecation import MiddlewareMixin
from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

# The API path prefix that precedes the org slug in the JSON API (openapi.yaml).
API_PREFIX_SEGMENTS = ("api", "v1")

# Candidate segments that are NEVER tenant slugs (served from the public schema). For the
# HTML family these are top-level prefixes; for the API family the relevant one is `auth`
# (the only non-tenant `/api/v1/...` group in v1). Kept aligned with
# apps.organizations.models.RESERVED_SLUGS (which blocks org creation from claiming these).
PUBLIC_CANDIDATES = frozenset(
    {
        "",              # the base landing page ("/") / "/api/v1/" with nothing after
        "healthz",
        "django-admin",
        "static",
        "media",
        "favicon.ico",
        "auth",          # /api/v1/auth/* — signup/verify/login/reset (public schema)
        "landing",       # /landing — public marketing page alias
        "app",           # /app — signed-in app entrypoint (old `/` dispatcher)
        "api",           # defensive: a bare "/api/..." that isn't "/api/v1/<slug>/"
    }
)


class TenantSlugMiddleware(MiddlewareMixin):
    """Resolve the tenant from the org slug in the URL path (Organization.slug). ADR-001.

    Must be placed at the TOP of MIDDLEWARE so the schema is active before sessions/auth
    read tenant-scoped data.
    """

    def process_request(self, request):
        # Tenant metadata lives in the public schema; always reset there first.
        connection.set_schema_to_public()

        candidate, strip_prefix = self._candidate_segment(request.path_info)

        # Public/reserved candidate → stay public.
        if candidate in PUBLIC_CANDIDATES:
            self._route_public(request)
            return None

        # Otherwise treat the candidate as an org slug.
        from apps.organizations.models import Organization

        with schema_context(get_public_schema_name()):
            try:
                org = Organization.objects.get(slug=candidate)
            except Organization.DoesNotExist:
                # Do not reveal whether a tenant exists — 404 like django-tenants.
                # `from None` suppresses the DoesNotExist chain (not an internal error).
                raise Http404(f'No organization for slug "{candidate}"') from None

        # Activate the org's schema.
        request.tenant = org
        connection.set_tenant(org)

        # Strip ONLY the slug from the path, preserving any API prefix in front of it, so:
        #   "/acme/healthz"      -> "/healthz"
        #   "/acme"              -> "/"
        #   "/api/v1/acme/org"   -> "/api/v1/org"
        #   "/api/v1/acme"       -> "/api/v1"
        remainder = strip_prefix + request.path_info[len(strip_prefix) + 1 + len(candidate):]
        if not remainder:
            remainder = "/"
        request.path_info = remainder
        request.path = remainder  # keep consistent for downstream code/logging

        # Use the tenant urlconf (ROOT_URLCONF) for this request.
        from django.conf import settings

        request.urlconf = settings.ROOT_URLCONF
        set_urlconf(settings.ROOT_URLCONF)
        return None

    @staticmethod
    def _candidate_segment(path: str) -> tuple[str, str]:
        """Return (candidate_slug_segment, prefix_before_the_slug).

        For the API family `/api/v1/<slug>/...` the prefix is `/api/v1` and the candidate is
        the segment after it. For everything else the prefix is `` and the candidate is the
        first path segment.
        """
        segments = path.strip("/").split("/")
        if len(segments) >= len(API_PREFIX_SEGMENTS) and tuple(
            segments[: len(API_PREFIX_SEGMENTS)]
        ) == API_PREFIX_SEGMENTS:
            candidate = segments[len(API_PREFIX_SEGMENTS)] if len(segments) > len(
                API_PREFIX_SEGMENTS
            ) else ""
            return candidate, "/" + "/".join(API_PREFIX_SEGMENTS)
        return (segments[0] if segments else ""), ""

    @staticmethod
    def _route_public(request):
        from django.conf import settings

        # Parity with django-tenants: expose the public tenant on the request so downstream
        # code can read request.tenant unconditionally.
        tenant_model = get_tenant_model()
        try:
            with schema_context(get_public_schema_name()):
                request.tenant = tenant_model.objects.get(
                    schema_name=get_public_schema_name()
                )
        except tenant_model.DoesNotExist:
            # No public tenant row (e.g. before any tenant bootstrap) — leave unset rather
            # than fail; public views here don't depend on request.tenant.
            pass

        public_urlconf = getattr(settings, "PUBLIC_SCHEMA_URLCONF", None)
        if public_urlconf:
            request.urlconf = public_urlconf
            set_urlconf(public_urlconf)
