"""
Workspace + Environment URLs (contracts/openapi.yaml — Workspaces tag).

TENANT-prefixed routes — included by config/urls.py under ``api/v1/`` AFTER
TenantSlugMiddleware strips the org slug. So request ``/api/v1/<slug>/workspaces`` arrives
here as ``api/v1/workspaces``:

  GET/POST     api/v1/workspaces                                  → WorkspaceListCreateView
  GET/PATCH/DELETE api/v1/workspaces/<uuid:workspace_id>          → WorkspaceDetailView
  GET          api/v1/workspaces/<uuid:workspace_id>/environments → WorkspaceEnvironmentsView
"""
from django.urls import path

from . import views

app_name = "workspaces"

urlpatterns = [
    path("workspaces", views.WorkspaceListCreateView.as_view(), name="workspace-list"),
    path(
        "workspaces/<uuid:workspace_id>",
        views.WorkspaceDetailView.as_view(),
        name="workspace-detail",
    ),
    path(
        "workspaces/<uuid:workspace_id>/environments",
        views.WorkspaceEnvironmentsView.as_view(),
        name="workspace-environments",
    ),
]
