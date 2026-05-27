"""
PUBLIC widget URLs (FEATURE_PARITY WS-11). Mounted by config/urls.py under ``api/v1/public/``
AFTER TenantSlugMiddleware strips the org slug. So the openapi-shaped paths
``/api/v1/<org_slug>/public/...`` arrive here with the slug gone:

  POST  api/v1/public/environments/<env>/actions/track                  → WidgetTrackView
  GET   api/v1/public/environments/<env>/app-surveys                      → WidgetAppSurveysView
  POST  api/v1/public/environments/<env>/app-surveys/<survey>/displays    → WidgetDisplayView

These are the unauthenticated, rate-limited widget endpoints. The app-survey impression endpoint
(WidgetDisplayView) keys the Display to the userId-resolved Contact (so displayLimit + recontact
cooldown count correctly) — the link-token-based POST /public/surveys/{id}/displays in
apps/responses is for the LINK runtime only. The survey-definition GET + response
create/patch/finalize that the widget ALSO calls live in apps/surveys/public_urls.py +
apps/responses/public_urls.py — the widget reuses that runtime for rendering + submission.
"""
from django.urls import path

from . import public_views

app_name = "actions_public"

urlpatterns = [
    path(
        "public/environments/<uuid:environment_id>/actions/track",
        public_views.WidgetTrackView.as_view(),
        name="widget-track",
    ),
    path(
        "public/environments/<uuid:environment_id>/app-surveys",
        public_views.WidgetAppSurveysView.as_view(),
        name="widget-app-surveys",
    ),
    path(
        "public/environments/<uuid:environment_id>/app-surveys/<uuid:survey_id>/displays",
        public_views.WidgetDisplayView.as_view(),
        name="widget-app-survey-display",
    ),
]
