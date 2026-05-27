"""
Teams HTMX (server-rendered) page + interactive endpoints — TENANT-scoped.

Mounted by config/urls.py under /<org_slug>/ AFTER TenantSlugMiddleware strips the slug, so
``/<org_slug>/teams`` arrives here as ``teams``. Namespace: ``teams_htmx``.

  GET/POST  teams                                     → teams_page         (list/matrix; POST = create team)
  POST      teams/<uuid:team_id>/access               → workspace_access   (HTMX grant/revoke → row partial)
  POST      teams/<uuid:team_id>/members              → team_add_member    (HTMX add → members partial)
  POST      teams/<uuid:team_id>/members/<uuid:user_id>/remove → team_remove_member (HTMX → members partial)
"""
from django.urls import path

from . import views_htmx

app_name = "teams_htmx"

urlpatterns = [
    path("teams", views_htmx.teams_page, name="teams"),
    path(
        "teams/<uuid:team_id>/access",
        views_htmx.workspace_access,
        name="workspace-access",
    ),
    path(
        "teams/<uuid:team_id>/members",
        views_htmx.team_add_member,
        name="member-add",
    ),
    path(
        "teams/<uuid:team_id>/members/<uuid:user_id>/remove",
        views_htmx.team_remove_member,
        name="member-remove",
    ),
]
