"""
Team / TeamMembership / WorkspaceTeam URLs (contracts/openapi.yaml — Teams tag).

TENANT-prefixed — included by config/urls.py under ``api/v1/`` AFTER TenantSlugMiddleware
strips the org slug. So ``/api/v1/<slug>/teams`` arrives here as ``api/v1/teams``:

  GET/POST     api/v1/teams                                       → TeamListCreateView
  GET/PATCH/DELETE api/v1/teams/<uuid:team_id>                    → TeamDetailView
  POST         api/v1/teams/<uuid:team_id>/members               → TeamMemberListView
  DELETE       api/v1/teams/<uuid:team_id>/members/<uuid:user_id> → TeamMemberDetailView
  POST         api/v1/teams/<uuid:team_id>/workspace-access      → TeamWorkspaceAccessView
"""
from django.urls import path

from . import views

app_name = "teams"

urlpatterns = [
    path("teams", views.TeamListCreateView.as_view(), name="team-list"),
    path("teams/<uuid:team_id>", views.TeamDetailView.as_view(), name="team-detail"),
    path(
        "teams/<uuid:team_id>/members",
        views.TeamMemberListView.as_view(),
        name="team-member-list",
    ),
    path(
        "teams/<uuid:team_id>/members/<uuid:user_id>",
        views.TeamMemberDetailView.as_view(),
        name="team-member-detail",
    ),
    path(
        "teams/<uuid:team_id>/workspace-access",
        views.TeamWorkspaceAccessView.as_view(),
        name="team-workspace-access",
    ),
]
