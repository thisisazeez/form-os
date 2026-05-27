"""
ActionClass management URLs (FEATURE_PARITY WS-11 "Actions / Events").

TENANT-prefixed routes — included by config/urls.py under ``api/v1/`` AFTER
TenantSlugMiddleware strips the org slug. So request
``/api/v1/<slug>/environments/<env_id>/actions`` arrives here as
``api/v1/environments/<env_id>/actions``:

  GET/POST          api/v1/environments/<uuid:environment_id>/actions → ActionClassListCreateView
  GET/PATCH/DELETE  api/v1/actions/<uuid:action_id>                   → ActionClassDetailView
"""
from django.urls import path

from . import views

app_name = "actions"

urlpatterns = [
    path(
        "environments/<uuid:environment_id>/actions",
        views.ActionClassListCreateView.as_view(),
        name="action-list",
    ),
    path(
        "actions/<uuid:action_id>",
        views.ActionClassDetailView.as_view(),
        name="action-detail",
    ),
]
