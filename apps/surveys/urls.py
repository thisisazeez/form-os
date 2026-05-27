"""
Survey + lifecycle URLs (contracts/openapi.yaml — Surveys tag).

TENANT-prefixed routes — included by config/urls.py under ``api/v1/`` AFTER
TenantSlugMiddleware strips the org slug. So request
``/api/v1/<slug>/environments/<env_id>/surveys`` arrives here as
``api/v1/environments/<env_id>/surveys``:

  GET/POST          api/v1/environments/<uuid:environment_id>/surveys → SurveyListCreateView
  GET/PUT/DELETE    api/v1/surveys/<uuid:survey_id>                   → SurveyDetailView
  POST              api/v1/surveys/<uuid:survey_id>/status            → SurveyStatusView
"""
from django.urls import path

from . import views

app_name = "surveys"

urlpatterns = [
    path(
        "environments/<uuid:environment_id>/surveys",
        views.SurveyListCreateView.as_view(),
        name="survey-list",
    ),
    path(
        "surveys/<uuid:survey_id>",
        views.SurveyDetailView.as_view(),
        name="survey-detail",
    ),
    path(
        "surveys/<uuid:survey_id>/status",
        views.SurveyStatusView.as_view(),
        name="survey-status",
    ),
]
