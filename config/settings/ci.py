"""
onForm — CI settings.

Used by GitHub Actions (`DJANGO_SETTINGS_MODULE=config.settings.ci`, see
`.github/workflows/ci.yml`). Inherits base and applies test-runner-friendly overrides.
`DATABASE_URL` / `SECRET_KEY` are supplied by the CI environment (Postgres service container).
"""
from .base import *  # noqa: F401,F403

DEBUG = False

# Django's test client uses "testserver"; keep localhost for any live checks in CI.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", ".localhost", "testserver"]

# No live broker in CI — run Celery tasks inline so tasks don't need a worker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# In-memory email so Wave-2 auth tests (verification / invitations) can assert on mail.outbox.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Speed up auth-heavy tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
