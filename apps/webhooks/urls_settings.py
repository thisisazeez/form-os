"""
Webhook management HTMX (server-rendered) page URLs — TENANT-scoped.

Mounted by config/urls.py under /<org_slug>/ AFTER TenantSlugMiddleware strips the slug:
  /<org_slug>/settings/webhooks  → webhooks_page  (list + create + update + delete)
Namespace: ``webhooks_htmx``.
"""
from django.urls import path

from . import views_settings

app_name = "webhooks_htmx"

urlpatterns = [
    path("settings/webhooks", views_settings.webhooks_page, name="webhooks"),
]
