"""
Audit-log viewer HTMX (server-rendered) page URLs — TENANT-scoped.

Mounted by config/urls.py under /<org_slug>/ AFTER TenantSlugMiddleware strips the slug:
  /<org_slug>/settings/audit-log  → audit_log_page  (paginated, filterable list)
Namespace: ``audit_htmx``.
"""
from django.urls import path

from . import views_settings

app_name = "audit_htmx"

urlpatterns = [
    path("settings/audit-log", views_settings.audit_log_page, name="audit-log"),
]
