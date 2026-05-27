"""
Org settings/billing + account-settings HTMX page URLs — TENANT-scoped.

A NEW urlconf (kept separate from apps.organizations.urls so the existing API + members-HTMX
routes are untouched). Mounted by config/urls.py under /<org_slug>/ AFTER TenantSlugMiddleware
strips the slug. Namespace: ``org_settings_htmx``.
  /<org_slug>/settings          → org_settings_page   (section="general")
  /<org_slug>/settings/billing  → org_settings_page   (section="billing")
  /<org_slug>/account           → account_settings_page
"""
from django.urls import path

from . import views_settings

app_name = "org_settings_htmx"

urlpatterns = [
    path("settings", views_settings.org_settings_page, {"section": "general"}, name="settings"),
    path(
        "settings/billing",
        views_settings.org_settings_page,
        {"section": "billing"},
        name="billing",
    ),
    path("account", views_settings.account_settings_page, name="account"),
]
