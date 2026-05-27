"""
PUBLIC response WRITE URLs (openapi "Public Survey" tag — response create/patch/finalize/display).

Mounted by config/urls.py under ``api/v1/public/`` AFTER TenantSlugMiddleware strips the org
slug. So the openapi paths ``/api/v1/{org_slug}/public/...`` arrive here with the slug gone:

  POST  api/v1/public/surveys/<uuid:survey_id>/responses          → PublicResponseCreateView
  PATCH api/v1/public/responses/<uuid:response_id>                → PublicResponseDetailView
  POST  api/v1/public/responses/<uuid:response_id>/finalize       → PublicResponseFinalizeView
  POST  api/v1/public/surveys/<uuid:survey_id>/displays           → PublicDisplayView

The survey-definition GET lives in apps/surveys/public_urls.py. The presigned upload-url
endpoint is DEFERRED (a follow-on) and is intentionally NOT routed — a respondent hitting it
gets a 404, not a half-implemented surface.
"""
from django.urls import path

from . import public_views

app_name = "responses_public"

urlpatterns = [
    path(
        "public/surveys/<uuid:survey_id>/responses",
        public_views.PublicResponseCreateView.as_view(),
        name="public-response-create",
    ),
    path(
        "public/responses/<uuid:response_id>",
        public_views.PublicResponseDetailView.as_view(),
        name="public-response-detail",
    ),
    path(
        "public/responses/<uuid:response_id>/finalize",
        public_views.PublicResponseFinalizeView.as_view(),
        name="public-response-finalize",
    ),
    path(
        "public/surveys/<uuid:survey_id>/displays",
        public_views.PublicDisplayView.as_view(),
        name="public-survey-displays",
    ),
]
