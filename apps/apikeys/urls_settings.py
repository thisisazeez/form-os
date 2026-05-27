"""
API-key management HTMX (server-rendered) page URLs — TENANT-scoped.

Mounted by config/urls.py under /<org_slug>/ AFTER TenantSlugMiddleware strips the slug:
  /<org_slug>/settings/api-keys  → api_keys_page  (list + create + revoke/delete)
Namespace: ``apikeys_htmx``.
"""
from django.urls import path

from . import views_settings

app_name = "apikeys_htmx"

urlpatterns = [
    path("settings/api-keys", views_settings.api_keys_page, name="api-keys"),
]
