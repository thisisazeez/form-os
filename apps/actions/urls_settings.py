"""
Actions + Website/App Connection management page URLs — TENANT-scoped (FEATURE_PARITY WS-11).

Mounted by config/urls.py under /<org_slug>/ AFTER TenantSlugMiddleware strips the slug:
  /<org_slug>/settings/actions     → actions_page     (list + create + update + delete)
  /<org_slug>/settings/connection  → connection_page  (env id + embed snippet; read-only)
Namespace: ``actions_htmx`` (mirrors webhooks_htmx; these are server-rendered, not the DRF API).
"""
from django.urls import path

from . import views_settings

app_name = "actions_htmx"

urlpatterns = [
    path("settings/actions", views_settings.actions_page, name="actions"),
    path("settings/connection", views_settings.connection_page, name="connection"),
]
