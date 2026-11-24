"""
onForm — development settings.

Imports everything from base and relaxes a few things for local work. Used by
`manage.py` (default) and pytest (DJANGO_SETTINGS_MODULE in pyproject.toml).
"""
from .base import *  # noqa: F401,F403
from .base import INSTALLED_APPS, env

DEBUG = env("DEBUG", default=True)

# Lenient hosts in dev (includes *.localhost so path-based + future subdomain both work).
ALLOWED_HOSTS = ["localhost", "127.0.0.1", ".localhost", "0.0.0.0", "testserver"]

# Run Celery tasks synchronously in dev/tests unless a worker is explicitly wanted —
# avoids needing a live broker for the Phase-0 skeleton.
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER", default=True)
CELERY_TASK_EAGER_PROPAGATES = True

# Optional dev tooling (django-extensions). Guarded so a missing install doesn't break boot.
try:
    import django_extensions  # noqa: F401

    INSTALLED_APPS = INSTALLED_APPS + ["django_extensions"]
except ImportError:
    pass

# Email: base.py is authoritative (env-driven SMTP; console backend by default — zero-setup).
# Dev only points the in-email link base URL at the local server (override via PUBLIC_BASE_URL).
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="http://127.0.0.1:8011")
