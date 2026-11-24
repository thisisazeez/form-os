"""
Project-wide views for the Phase-0 skeleton (PLAN §3 frontend shell).

  - landing:     public-schema marketing/landing page (proves the HTMX/Tailwind shell renders).
  - healthz:     liveness/readiness check (used by the smoke tests + ops). Returns JSON 200.
  - tenant_home: rendered inside a resolved org (proves ADR-001 path-based routing works
                 end-to-end) and hosts the two React island placeholders (proves the
                 Vite + django-vite pipeline mounts builder + runtime — PLAN §8).
  - app_shell_smoke: Wave-0F TEMPORARY fixture — a minimal authenticated page that extends
                 templates/_layouts/app_shell.html so the shell can be exercised end-to-end by
                 tests/test_app_shell.py BEFORE any real admin page is migrated to the shell.
                 DELETE this view + its url + templates/_smoke/app_shell_smoke.html once a real
                 admin page (e.g. the Surveys list) extends the shell.
"""
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse


def landing(request):
    """App entrypoint (served at ``/app``) — the old behavior of the base URL.

    - Anonymous visitor → the login page (``/auth/login``).
    - Signed-in visitor → straight into the app: their first active-org dashboard
      (``/<slug>/``), or the create-org page if they have no org yet.

    ``/`` now serves the public marketing page (``marketing_landing``); this view is kept
    under ``/app`` so existing post-login deep links and auth flows still have a stable home.
    """
    if request.user.is_authenticated:
        # Local import: organizations is a separate app; keep common.views import-light.
        from apps.organizations.services import first_active_org

        org = first_active_org(request.user)
        if org is not None:
            return redirect(f"/{org.slug}/")
        return redirect(reverse("org_public_htmx:org-create"))
    return redirect(reverse("accounts_htmx:login"))


def marketing_landing(request):
    """Public marketing homepage (``/``, alias ``/landing``) — branded onForm template.

    The signed-in app entrypoint remains available at ``/app`` (``landing``), so auth flows
    that used to point at ``/`` are preserved without taking over the marketing homepage.
    """
    return render(request, "landing.html")


def healthz(request):
    """Health check. Reports the active schema so tenant-routing can be verified by ops/tests.

    `connection.schema_name` is 'public' on the base domain and the org's schema_name when
    a `/<org_slug>/` prefix resolved a tenant (ADR-001).
    """
    return JsonResponse(
        {
            "status": "ok",
            "service": "opensurvey",
            "schema": getattr(connection, "schema_name", "public"),
        }
    )


def tenant_home(request):
    """Tenant landing, rendered after TenantSlugMiddleware activated an org schema.

    Hosts the two stubbed React islands (builder + runtime) so the Vite pipeline is proven.
    `vite_built` tells the template whether a production manifest exists, so the page
    renders cleanly even before `npm run build` has been run (e.g. in CI without Node) —
    the island mount points are shown either way; the bundled JS loads only when present.
    """
    from django.conf import settings

    tenant = getattr(connection, "tenant", None)
    vite_conf = settings.DJANGO_VITE.get("default", {})
    manifest_path = vite_conf.get("manifest_path")
    vite_built = bool(vite_conf.get("dev_mode")) or (
        manifest_path is not None and manifest_path.exists()
    )

    context = {
        "org_name": getattr(tenant, "name", None),
        "org_slug": getattr(tenant, "slug", None),
        "schema": getattr(connection, "schema_name", "public"),
        "vite_built": vite_built,
    }
    return render(request, "tenant_home.html", context)


@login_required
def search_page(request):
    """``/<org_slug>/search?q=`` — global search across surveys (by name) and contacts
    (by attribute value, e.g. email / name).

    Scoping mirrors the rest of the app so nothing leaks across workspaces:
      - surveys → the caller's CURRENT workspace environments (same as the dashboard / list).
      - contacts → every environment the caller can READ (same as the contacts list), and each
        matched attribute value is masked when the caller lacks PII-unmasked access to that
        environment (apps.contacts.scoping) — so search never reveals more than the contacts page.
    """
    from django.http import Http404
    from django_tenants.utils import get_public_schema_name

    org = getattr(request, "tenant", None)
    if org is None or getattr(org, "schema_name", None) == get_public_schema_name():
        raise Http404("Organization not resolved.")

    query = (request.GET.get("q") or "").strip()
    surveys, contacts = [], []

    if query:
        from apps.common.active_workspace import current_workspace_env_ids
        from apps.surveys.models import Survey

        env_ids = current_workspace_env_ids(request)
        if env_ids:
            surveys = list(
                Survey.objects.filter(environment_id__in=env_ids, name__icontains=query)
                .order_by("-updated_at", "id")[:25]
            )

        from apps.contacts.models import ContactAttribute
        from apps.contacts.scoping import pii_unmasked_for_environment, readable_environment_ids

        readable = readable_environment_ids(request.user)
        if readable:
            attrs = (
                ContactAttribute.objects.filter(
                    contact__environment_id__in=readable, value__icontains=query
                )
                .select_related("contact", "contact__environment", "attribute_key")
                .order_by("-contact__created_at")[:80]
            )
            unmasked_cache: dict = {}
            seen: set = set()
            for attr in attrs:
                if attr.contact_id in seen:
                    continue
                seen.add(attr.contact_id)
                env = attr.contact.environment
                if env.id not in unmasked_cache:
                    unmasked_cache[env.id] = pii_unmasked_for_environment(request.user, env)
                show_value = unmasked_cache[env.id] or not attr.attribute_key.is_pii
                contacts.append(
                    {
                        "id": attr.contact_id,
                        "key": attr.attribute_key.key,
                        "value": attr.value if show_value else "•••",
                    }
                )
                if len(contacts) >= 25:
                    break

    return render(
        request,
        "search/results.html",
        {"org": org, "section": "search", "query": query, "surveys": surveys, "contacts": contacts},
    )


@login_required
def app_shell_smoke(request):
    """Wave-0F TEMPORARY smoke fixture (see module docstring).

    Renders a minimal authenticated page that extends the admin app shell so
    tests/test_app_shell.py can assert the shell renders (logo, sidebar nav, brand tokens, no
    `indigo`) through the real TenantSlugMiddleware path. ``@login_required`` mirrors the
    canonical admin-page gate; this fixture has no RBAC of its own (it shows no tenant data).

    DELETE once a real admin page extends the shell.
    """
    return render(request, "_smoke/app_shell_smoke.html", {})
