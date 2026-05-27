"""
Phase-0 smoke tests (per the scaffold brief §8).

Covers: settings import, the project boots, migrate_schemas works, a demo tenant + domain
can be created (schema provisioned + tenant migrations run), and the health/landing pages
return 200 — on BOTH the public schema and inside a routed tenant.
"""
import pytest
from django.db import connection
from django.test import Client


# ---------------------------------------------------------------------------
# Boot / settings (no DB)
# ---------------------------------------------------------------------------
def test_settings_import():
    """Settings module imports and the django-tenants split is configured."""
    from django.conf import settings

    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert settings.TENANT_MODEL == "organizations.Organization"
    assert settings.TENANT_DOMAIN_MODEL == "organizations.Domain"
    # SHARED vs TENANT split (models.md PART B).
    assert "apps.accounts" in settings.SHARED_APPS
    assert "apps.organizations" in settings.SHARED_APPS
    assert "django_tenants" in settings.SHARED_APPS
    for tenant_app in ("apps.surveys", "apps.responses", "apps.teams", "apps.contacts"):
        assert tenant_app in settings.TENANT_APPS
    # Custom path-routing middleware is first (ADR-001).
    assert settings.MIDDLEWARE[0] == "apps.common.middleware.TenantSlugMiddleware"


def test_project_boots_system_check():
    """`manage.py check` equivalent: Django app registry loads with no errors."""
    from django.core.management import call_command

    # Raises SystemCheckError on any error-level issue; warnings (vite manifest) are OK.
    call_command("check")


def test_uuid_pk_and_can_signature_present():
    """Frozen scaffold infrastructure exists: UUID base model + can() entry point.

    Wave 2b (Phase 2, this producer) replaced the Phase-0 ``NotImplementedError`` stub with
    the real RBAC engine (contracts/rbac.md). The frozen contract now in force: anonymous/None
    user → False, and (per rbac.md §7 step ordering) the anonymous check runs BEFORE the
    action lookup, so ``can(None, ...)`` returns False without raising even for an unknown
    action. The full policy matrix — including unknown-action ValueError for an authenticated
    user — is covered in tests/test_rbac_engine.py.
    """
    from apps.common.models import TimeStampedUUIDModel
    from apps.common.permissions import can

    # UUID PK base (ADR-003).
    pk_field = TimeStampedUUIDModel._meta.get_field("id")
    assert pk_field.primary_key is True
    # can() is implemented (no longer a NotImplementedError stub): anonymous → False, no raise.
    assert can(None, "view_survey", None) is False
    assert can(None, "view_org") is False


# ---------------------------------------------------------------------------
# Public schema (DB)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_root_redirects_anonymous_to_login():
    # The base URL is the login page, not a marketing landing page: an anonymous visitor to
    # `/` is redirected to /auth/login (apps.common.views.landing).
    client = Client()
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp["Location"] == "/auth/login"


@pytest.mark.django_db
def test_public_healthz_returns_200_public_schema():
    client = Client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["schema"] == "public"


# ---------------------------------------------------------------------------
# Tenant creation + routing (DB)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_demo_tenant_and_domain_created(demo_tenant):
    """A demo tenant + domain exist and the tenant schema was provisioned."""
    from apps.organizations.models import Domain, Organization

    assert Organization.objects.filter(slug="demo").exists()
    assert Domain.objects.filter(domain="localhost", tenant=demo_tenant).exists()

    # The tenant's Postgres schema physically exists.
    connection.set_schema_to_public()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", ["demo"]
        )
        assert cur.fetchone() is not None


@pytest.mark.django_db
def test_tenant_routing_activates_schema(demo_tenant):
    """Requesting /<slug>/healthz resolves the tenant and activates its schema (ADR-001)."""
    connection.set_schema_to_public()
    client = Client()
    resp = client.get("/demo/healthz")
    assert resp.status_code == 200
    assert resp.json()["schema"] == "demo"


@pytest.mark.django_db
def test_tenant_home_redirects_anonymous_to_login(demo_tenant):
    """The tenant home (``/<slug>/``) is now the WIRED admin dashboard (Wave 3b, A1
    surveys-admin) and is ``@login_required``, so an anonymous visitor is redirected to the
    login page (replacing the old island-smoke ``tenant_home``, which rendered the Vite mount
    points anonymously). The dashboard's authenticated rendering + RBAC scoping is covered in
    tests/test_surveys_admin.py; here we only assert the login gate is present after the
    sanctioned repoint. Tenant routing itself is exercised by test_tenant_routing_activates_schema.
    """
    connection.set_schema_to_public()
    client = Client()
    resp = client.get("/demo/")
    assert resp.status_code in (301, 302)


@pytest.mark.django_db
def test_unknown_tenant_404_no_leak(demo_tenant):
    """An unknown org slug 404s (no tenant-existence oracle)."""
    connection.set_schema_to_public()
    client = Client()
    resp = client.get("/doesnotexist/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_api_family_routing_activates_tenant(demo_tenant):
    """openapi.yaml shape `/api/v1/<org_slug>/...` resolves the tenant (slug after api/v1)."""
    connection.set_schema_to_public()
    client = Client()
    resp = client.get("/api/v1/demo/healthz")
    assert resp.status_code == 200
    assert resp.json()["schema"] == "demo"


@pytest.mark.django_db
def test_api_auth_path_stays_public(demo_tenant):
    """`/api/v1/auth/...` is the PUBLIC auth API — `auth` is not treated as an org slug.

    Wave 2a builds the signup endpoint (POST). A GET reaches that PUBLIC-urlconf view and
    returns 405 (Method Not Allowed) — crucially NOT a tenant-resolution 404 and NOT a
    schema switch. If `auth` had been treated as an org slug, the middleware would have
    raised the org-not-found 404 BEFORE any view ran. A 405 therefore proves the request
    stayed public and resolved the public auth view.
    """
    connection.set_schema_to_public()
    client = Client()
    resp = client.get("/api/v1/auth/signup")
    # 405 (POST-only view) — it stayed public (no tenant 'auth'); not a tenant 404.
    assert resp.status_code == 405
    # The public landing/health still works in the same client (schema stayed public).
    assert client.get("/healthz").json()["schema"] == "public"


def test_reserved_slugs_include_routing_collisions():
    """Reserved slugs cover the routing collisions the middleware relies on."""
    from apps.organizations.models import RESERVED_SLUGS

    for reserved in ("api", "auth", "s", "static", "healthz", "django-admin"):
        assert reserved in RESERVED_SLUGS


@pytest.mark.django_db(transaction=True)
def test_reserved_slug_rejected_on_create():
    """Reserved-slug enforcement runs on the .create()/.save() path, not just full_clean()
    (reviewer F1). A tenant must NOT be able to claim e.g. `api` and provision a schema that
    shadows the public auth/API paths.
    """
    from django.core.exceptions import ValidationError

    from apps.organizations.models import Organization

    connection.set_schema_to_public()
    with pytest.raises(ValidationError):
        Organization.objects.create(name="Evil", slug="api", schema_name="api")

    # And the reserved schema was NOT provisioned (the save was blocked before TenantMixin
    # could create it).
    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", ["api"]
        )
        assert cur.fetchone() is None


@pytest.mark.django_db(transaction=True)
def test_valid_slug_still_saves():
    """A non-reserved slug still creates the tenant + schema (full_clean() didn't over-block)."""
    from apps.organizations.models import Domain, Organization

    connection.set_schema_to_public()
    Organization.objects.filter(slug="acme").delete()
    org = Organization.objects.create(name="Acme", slug="acme", schema_name="acme")
    Domain.objects.create(tenant=org, domain="localhost", is_primary=True)
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", ["acme"]
            )
            assert cur.fetchone() is not None
    finally:
        connection.set_schema_to_public()
        org.delete(force_drop=True)
