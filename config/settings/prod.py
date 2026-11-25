"""Production settings — used by the Render and Vercel API deployments.

Inherits everything from :mod:`config.settings.base` and only adds the hardening /
static-serving pieces that are inappropriate for local development:

  * DEBUG forced off (never trust an env var to turn it on in prod)
  * secure cookies / HSTS behind the platform TLS terminator
  * WhiteNoise serves collected static assets (Vite bundle + brand fonts/SVGs)
  * CELERY_TASK_ALWAYS_EAGER must be explicitly disabled unless a worker is deployed

Deployments run ``migrate_schemas --shared`` and ``create_demo_tenant`` as a separate
release step (see deploy/render/ and deploy/vercel/) — never on the request path.
"""
from .base import *  # noqa: F401,F403
from .base import MIDDLEWARE, env

# TEMPORARY deployment diagnostic: DEBUG=true shows Django's detailed error page so a
# production-only 500 can be diagnosed. Restore to `False` once the incident is resolved.
DEBUG = env.bool("DEBUG", default=False)

# Platform reverse proxies terminate TLS and pass the original scheme in this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Leading slash keeps {% static %} and django-vite URLs absolute on tenant paths like /acme/.
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# WhiteNoise sits after Django's security middleware and before the URL resolver.
# CompressedManifestStaticFilesStorage emits hashed, gzip/brotli-ready static files.
MIDDLEWARE = [
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *[m for m in MIDDLEWARE if m != "whitenoise.middleware.WhiteNoiseMiddleware"],
]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
# Keep the original filenames too: the built widget CSS references unhashed font URLs
# (it is injected into third-party pages and cannot use Django's {% static %} hashing).
WHITENOISE_KEEP_ONLY_HASHED_FILES = False

# Background jobs need a real broker in prod; dev/ci default to eager execution.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
