"""
Response READ URLs (contracts/openapi.yaml — Responses tag, read side only).

Wave 2c serves ONLY the authenticated read path. The public submission path
(POST/PATCH/finalize via token) is the runtime wave; tag/delete/export are deferred.

TENANT-prefixed — included by config/urls.py under ``api/v1/`` AFTER TenantSlugMiddleware
strips the org slug. So ``/api/v1/<slug>/surveys/<id>/responses`` arrives here as
``api/v1/surveys/<id>/responses``:

  GET   api/v1/surveys/<uuid:survey_id>/responses → ResponseListView
  GET   api/v1/responses/<uuid:response_id>       → ResponseDetailView
"""
from django.urls import path

from . import views

app_name = "responses"

urlpatterns = [
    path(
        "surveys/<uuid:survey_id>/responses",
        views.ResponseListView.as_view(),
        name="response-list",
    ),
    path(
        "responses/<uuid:response_id>",
        views.ResponseDetailView.as_view(),
        name="response-detail",
    ),
]
