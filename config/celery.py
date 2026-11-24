"""
Celery app for onForm (PLAN §13).

Wiring only in Phase 0: emails, exports, webhook delivery, and analytics rollups
land in later phases. Note: django-tenants + Celery needs tenant-aware task routing
(activate the right schema per task) — that integration is a Wave 2 concern; here we
just establish the app object so `celery -A config worker` resolves.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("onform")

# Read CELERY_* settings from Django config.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py modules across installed apps (Wave 2+).
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Trivial task to verify the worker is wired up."""
    print(f"Request: {self.request!r}")
