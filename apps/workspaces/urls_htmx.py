"""
Workspace settings HTMX (server-rendered) page URLs — TENANT-scoped.

Mounted by config/urls.py under /<org_slug>/ AFTER TenantSlugMiddleware strips the slug:
  /<org_slug>/settings/workspace                          → workspace_settings_page (default ws)
  /<org_slug>/workspaces/<uuid:id>/settings               → workspace_settings_page (addressable)
  /<org_slug>/settings/workspace/look-and-feel            → workspace_look_and_feel_page (default ws)
  /<org_slug>/workspaces/<uuid:id>/settings/look-and-feel → workspace_look_and_feel_page (addressable)
Namespace: ``workspaces_htmx``.
"""
from django.urls import path

from . import views_htmx

app_name = "workspaces_htmx"

urlpatterns = [
    path(
        "workspaces/new",
        views_htmx.create_workspace_page,
        name="workspace-create",
    ),
    path(
        "switch-workspace/<uuid:workspace_id>",
        views_htmx.switch_workspace,
        name="switch-workspace",
    ),
    path(
        "settings/workspace",
        views_htmx.workspace_settings_page,
        name="workspace-settings",
    ),
    path(
        "workspaces/<uuid:workspace_id>/settings",
        views_htmx.workspace_settings_page,
        name="workspace-settings-detail",
    ),
    # Look & Feel (project theme). Listed AFTER the bare-`settings` routes; Django path matching
    # is exact on the full remaining path, so `settings/workspace` never swallows
    # `settings/workspace/look-and-feel` — order is for readability, not correctness.
    path(
        "settings/workspace/look-and-feel",
        views_htmx.workspace_look_and_feel_page,
        name="workspace-look-and-feel",
    ),
    path(
        "workspaces/<uuid:workspace_id>/settings/look-and-feel",
        views_htmx.workspace_look_and_feel_page,
        name="workspace-look-and-feel-detail",
    ),
]
