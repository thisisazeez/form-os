"""
onForm project package.

Expose the Celery app so `@shared_task` works and `celery -A config` resolves.
The import is guarded so that environments without Celery installed (or during certain
management commands) still import the package cleanly.
"""
try:
    from .celery import app as celery_app

    __all__ = ("celery_app",)
except Exception:  # pragma: no cover - celery optional at import time
    __all__ = ()
