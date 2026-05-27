"""
OpenSurvey — base settings (shared by dev/prod).

Authoritative references:
  - PLAN.md §3 (architecture), §4 (tenancy), §13 (stack)
  - contracts/decisions.md ADR-001 (path-based routing), ADR-002 (cross-schema user_id),
    ADR-003 (UUID PKs), ADR-005 (Django 5.x + DRF)
  - contracts/models.md PART B (app → schema placement)

Multi-tenancy is provided by django-tenants: ONE Postgres schema per Organization.
SHARED_APPS live in the `public` schema; TENANT_APPS are migrated into each org schema.
"""
from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------
# config/settings/base.py -> config/settings -> config -> <repo root>
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", ".localhost"]),
    DJANGO_VITE_DEV_MODE=(bool, False),
    DJANGO_VITE_DEV_SERVER_PORT=(int, 5173),
    BASE_DOMAIN=(str, "localhost"),
)

# Load .env if present (local dev). In prod, env vars come from the environment.
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(str(_env_file))

# Safe-by-default so a fresh clone (or a container with only some env vars set) boots without a
# mandatory .env. dev.py overrides DEBUG/ALLOWED_HOSTS for local work; a real deployment MUST set
# SECRET_KEY, DEBUG=False, and ALLOWED_HOSTS via the environment.
SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
BASE_DOMAIN = env("BASE_DOMAIN", default="localhost")

# ---------------------------------------------------------------------------
# Applications — django-tenants SHARED vs TENANT split (contracts/models.md PART B)
# ---------------------------------------------------------------------------
# SHARED_APPS: migrated to the `public` schema; shared across all tenants.
# `django_tenants` MUST be first. The TENANT_MODEL app (organizations) and the
# custom-user app (accounts) live here.
SHARED_APPS = [
    "django_tenants",            # tenant routing/management — must precede contrib
    "apps.organizations",        # Organization (TENANT_MODEL), Domain, Membership, Invitation
    "apps.accounts",             # custom User (AUTH_USER_MODEL)
    # Shared lib app — NO concrete models (only abstract base mixins; models.md PART B).
    # Registered here (Wave 2b) so Django discovers apps/common/templatetags/ (the `{% can %}`
    # assignment tag, rbac.md §8.3) — template-tag libraries load ONLY from installed apps.
    # Safe: it ships no migrations (no concrete models), so it adds no public/tenant tables.
    "apps.common",


    # Django contrib (shared so admin/auth/sessions live in public schema)
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party that must be available in the public schema
    "rest_framework",
    "django_vite",
]

# TENANT_APPS: migrated into each Organization's schema (per-org, isolated).
# Skeletons in Wave 1; business logic lands in Wave 2 (contracts/models.md PART 2).
TENANT_APPS = [
    # contrib needed inside tenant schemas
    "django.contrib.contenttypes",
    "django.contrib.auth",

    "apps.workspaces",    # Workspace, Environment (contracts/models.md §2.1, §2.2)
    "apps.environments",  # placeholder package (Environment model lives in workspaces — see app docstring)
    "apps.teams",         # Team, TeamMembership, WorkspaceTeam (§2.3–2.5)
    "apps.surveys",       # Survey (§2.6)
    "apps.actions",       # ActionClass — website/app-survey triggers (FEATURE_PARITY WS-11)
    "apps.responses",     # Response, SurveyLink, Display, Tag, ResponseTag (§2.7–2.9)
    "apps.followups",     # FollowUpDelivery — survey follow-up automations (WS-4 parity)
    "apps.contacts",      # Contact, ContactAttributeKey, ContactAttribute (§2.10–2.12)
    "apps.segments",      # Segment (§2.13)
    "apps.webhooks",      # Webhook (§2.14)  [Phase 7]
    "apps.apikeys",       # ApiKey (§2.15)
    "apps.audit",         # AuditLog (§2.16)  [Phase 7]
    "apps.integrations",  # SlackIntegration — credential-free integrations hub (WS-10 parity)
]

# django-tenants requires INSTALLED_APPS = SHARED_APPS + (TENANT_APPS not already shared).
INSTALLED_APPS = list(SHARED_APPS) + [a for a in TENANT_APPS if a not in SHARED_APPS]

# ---------------------------------------------------------------------------
# django-tenants core configuration
# ---------------------------------------------------------------------------
TENANT_MODEL = "organizations.Organization"   # the TenantMixin model
TENANT_DOMAIN_MODEL = "organizations.Domain"  # the DomainMixin model

# ADR-001: path-based routing on a shared domain (`/<org_slug>/...`).
# A custom middleware (apps.common.middleware.TenantSlugMiddleware) resolves the tenant
# from the FIRST URL path segment against Organization.slug and activates its schema BEFORE
# auth/authz runs. We use a custom middleware (explicitly permitted by the Phase-0 brief)
# rather than django-tenants' TenantSubfolderMiddleware because the built-in (a) requires a
# non-empty TENANT_SUBFOLDER_PREFIX — which would force `/orgs/<slug>/...` and break the
# frozen `/<org_slug>/...` URL shape in openapi.yaml — and (b) resolves against the Domain
# table, not Organization.slug. See the middleware module docstring for the full rationale.
# The Domain model is retained (models.md §1.3) so a future switch to subdomain routing
# (ADR-001 "reversible") needs no data migration.

# Routing differs by schema: public URLs (auth, landing, health) vs tenant URLs.
ROOT_URLCONF = "config.urls"             # tenant urlconf (active inside an org)
PUBLIC_SCHEMA_URLCONF = "config.urls_public"  # public-schema urlconf (no tenant prefix)

# @login_required redirects here. Our login lives at /auth/login (apps.accounts.urls_htmx),
# NOT Django's default /accounts/login/ — without this, every gated page would bounce an
# anonymous visitor to a non-existent URL (404). The `?next=` round-trip is honored by
# login_page → _post_login_redirect.
LOGIN_URL = "/auth/login"
LOGIN_REDIRECT_URL = "/"  # `/` sends a signed-in user into their app home (see common.views.landing)

# ---------------------------------------------------------------------------
# Middleware — tenant resolution must come first (before auth/session use the schema)
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    # Resolves Organization from the URL path prefix and activates its schema (ADR-001).
    # MUST be first: the schema must be active before sessions/auth read tenant data.
    "apps.common.middleware.TenantSlugMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ---------------------------------------------------------------------------
# Templates (HTMX/Alpine admin shell + island host templates — PLAN §8)
# ---------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database — django-tenants Postgres backend (REQUIRED; no SQLite)
# ---------------------------------------------------------------------------
# DATABASE_URL provides connection params; the ENGINE is forced to the django-tenants
# backend, and the tenant-aware router is registered.
_db = env.db("DATABASE_URL", default="postgres://opensurvey:opensurvey@localhost:5432/opensurvey")
_db["ENGINE"] = "django_tenants.postgresql_backend"
DATABASES = {"default": _db}

DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"  # only affects models WITHOUT an explicit PK;
# every OpenSurvey model declares a UUID PK (ADR-003), so this default is essentially unused.

# ---------------------------------------------------------------------------
# Auth (PLAN §9; contracts/models.md §1.1)
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"   # custom email-login user (ADR / models.md §1.1)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Email — transactional auth/invitation mail (PLAN §9), HTML + plaintext.
# ---------------------------------------------------------------------------
# Postmark when a server token is present (the token doubles as the SMTP username AND
# password — Postmark's documented SMTP auth). Without a token we fall back to the console
# backend so local/dev and CI need no credentials and nothing breaks. The token comes from
# the environment / .env (gitignored) — never committed.
# Provider-agnostic SMTP, configured entirely via env (open-source: bring your own SMTP).
# Zero-setup default: the console backend, so local/dev/CI need NO credentials and nothing
# breaks. Set EMAIL_HOST (+ EMAIL_HOST_USER / EMAIL_HOST_PASSWORD) to send via ANY SMTP server.
# POSTMARK_SERVER_TOKEN is an OPTIONAL convenience that pre-fills Postmark's SMTP host/creds.
# All values come from the environment / .env (gitignored) — never committed. See .env.example.
POSTMARK_SERVER_TOKEN = env("POSTMARK_SERVER_TOKEN", default="")
# Postmark requires a message-stream header; only attached when explicitly set (empty = none).
POSTMARK_MESSAGE_STREAM = env(
    "POSTMARK_MESSAGE_STREAM", default="outbound" if POSTMARK_SERVER_TOKEN else ""
)
# Generic SMTP — any provider. A Postmark token (if set) pre-fills host/user/password.
EMAIL_HOST = env("EMAIL_HOST", default="smtp.postmarkapp.com" if POSTMARK_SERVER_TOKEN else "")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default=POSTMARK_SERVER_TOKEN)
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default=POSTMARK_SERVER_TOKEN)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=15)
# SMTP when a host is configured, else the console backend (zero-setup dev/CI).
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.smtp.EmailBackend"
        if EMAIL_HOST
        else "django.core.mail.backends.console.EmailBackend"
    ),
)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@example.com")
# Absolute base URL for links in emails (verify/reset/invite). apps.accounts.emails falls
# back to http://<BASE_DOMAIN>:8000 when this is unset.
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="")

# ---------------------------------------------------------------------------
# DRF (PLAN §12; contracts/openapi.yaml). Permission/throttle classes added in Wave 2.
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,  # mirrors openapi.yaml PageSize default
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # Programmatic API clients present a raw key in the `x-api-key` header; the
        # authenticator scopes it to its own org schema and resolves the creating User so
        # the existing can() RBAC runs unchanged (apps.apikeys.authentication).
        "apps.apikeys.authentication.ApiKeyAuthentication",
        # Session auth serves the HTMX/browser admin (contracts/openapi.yaml securitySchemes).
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Default-deny posture: lock down by default, open public runtime explicitly in Wave 2.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# ---------------------------------------------------------------------------
# Celery / Redis (PLAN §13) — wiring only; tasks land in later phases.
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/2")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"

# ---------------------------------------------------------------------------
# django-vite — React island pipeline (PLAN §8; frontend/)
# ---------------------------------------------------------------------------
# Two islands are built by Vite (builder, runtime) and mounted into Django templates.
# In dev with DJANGO_VITE_DEV_MODE=True, django-vite proxies to the Vite dev server (HMR).
# Otherwise it reads the production manifest under STATIC_ROOT/frontend/.manifest.
DJANGO_VITE = {
    "default": {
        "dev_mode": env("DJANGO_VITE_DEV_MODE"),
        "dev_server_port": env("DJANGO_VITE_DEV_SERVER_PORT"),
        "manifest_path": BASE_DIR / "frontend" / "dist" / ".vite" / "manifest.json",
    }
}

# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Vite build output is collected as static assets; src dir holds island entrypoints.
STATICFILES_DIRS = [
    BASE_DIR / "frontend" / "dist",
]
# Leading slash is REQUIRED: FileField.url does urljoin(MEDIA_URL, name); without it an avatar
# on /<slug>/account would resolve to /<slug>/media/... and the TenantSlugMiddleware would 404 it.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# I18N / TZ
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Sessions / security baseline (tightened in prod settings)
# ---------------------------------------------------------------------------
SESSION_COOKIE_NAME = "sessionid"  # matches openapi.yaml sessionAuth scheme
