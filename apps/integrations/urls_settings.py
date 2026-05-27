"""
Integrations hub HTMX (server-rendered) page URLs — TENANT-scoped.

Mounted by config/urls.py under /<org_slug>/ AFTER TenantSlugMiddleware strips the slug:
  /<org_slug>/settings/integrations  → integrations_page  (Slack connect/update/disconnect hub)
Namespace: ``integrations_htmx``.
"""
from django.urls import path

from . import views_settings

app_name = "integrations_htmx"

urlpatterns = [
    path("settings/integrations", views_settings.integrations_page, name="integrations"),
]
