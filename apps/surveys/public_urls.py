"""
PUBLIC survey-read URLs (openapi "Public Survey" tag — survey-definition GET).

Mounted by config/urls.py under ``api/v1/public/`` AFTER TenantSlugMiddleware strips the org
slug. So the openapi path ``/api/v1/{org_slug}/public/surveys/{survey_id}`` arrives here as
``api/v1/public/surveys/<survey_id>``:

  GET  api/v1/public/surveys/<uuid:survey_id> → PublicSurveyView   (PublicSurvey shape)

Only the survey-definition GET lives here; the public response WRITE endpoints (create/
patch/finalize/displays) are in apps/responses/public_urls.py. The presigned upload-url
endpoint is DEFERRED (a follow-on) and is intentionally NOT routed.
"""
from django.urls import path

from . import public_views

app_name = "surveys_public"

urlpatterns = [
    path(
        "public/surveys/<uuid:survey_id>",
        public_views.PublicSurveyView.as_view(),
        name="public-survey-detail",
    ),
]
