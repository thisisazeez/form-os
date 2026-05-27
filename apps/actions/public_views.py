"""
PUBLIC widget APIs (FEATURE_PARITY WS-11) — the unauthenticated, env-scoped, rate-limited
endpoints the embeddable JS widget calls. Mirror the public response runtime
(apps.responses.public_views): ``authentication_classes = []`` + ``AllowAny`` + per-IP/per-env
throttles. Mounted under ``api/v1/public/`` after TenantSlugMiddleware strips the org slug.

Two endpoints (full request/response spec in the module-level docstrings below):
  POST .../environments/<env>/actions/track   → WidgetTrackView   (record action + return surveys)
  GET  .../environments/<env>/app-surveys      → WidgetAppSurveysView (eligible app surveys now)

The matching engine + rules live in apps.actions.public_services (the single business-logic
home). The track/app-surveys views are the thin HTTP adapter: validate the body, load the env,
call the service, serialize. track does NOT record impressions; the widget records each SHOWN
app survey via the dedicated WidgetDisplayView (userId-keyed Display) and reuses the EXISTING
public response create/patch/finalize loop for submission (documented in public_services).
"""
from __future__ import annotations

from django.http import Http404
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView

from apps.workspaces.models import Environment

from . import public_services
from .public_serializers import (
    WidgetAppSurveySerializer,
    WidgetDisplaySerializer,
    WidgetTrackSerializer,
)
from .throttling import (
    WidgetAppSurveysThrottle,
    WidgetDisplayThrottle,
    WidgetTrackThrottle,
)


def _get_environment_or_404(environment_id) -> Environment:
    """Load an Environment (with its workspace for recontact_days) or 404. No auth — the public
    widget is anonymous; the env id IS the (unguessable UUID) scoping token."""
    try:
        return Environment.objects.select_related("workspace").get(pk=environment_id)
    except (Environment.DoesNotExist, ValueError) as exc:
        raise Http404("Environment not found.") from exc


class WidgetTrackView(APIView):
    """POST record a tracked user action + return the app-surveys that should now display.

    Path:   POST /api/v1/<org_slug>/public/environments/<env_id>/actions/track
    Body:   {
              "userId": "<client-supplied external user id>",   # required
              "action": "<action name or code key>",             # required
              "attributes": { "<k>": "<v>", ... },               # optional contact traits
              "url": "<current page url>"                          # optional (pageview context)
            }
    Returns 200: {
              "userId": "<echoed>",
              "person_id": "<resolved Contact uuid>",
              "action": "<echoed>",
              "matched_action": true|false,                       # did `action` resolve to an ActionClass
              "surveys": [                                          # eligible app surveys to display NOW
                 {"id","name","survey_type","triggers","display_options"}, ...
              ]
            }

    Behavior: upserts the person (Contact.external_id == userId) in the env, optionally merges
    attributes, resolves the action to an ActionClass (by config.key then name), and returns the
    published app-surveys whose triggers include that action AND that pass the per-user gates
    (displayLimit / recontact cooldown / displayPercentage — see public_services matching rules).
    An UNDEFINED action (no matching ActionClass) is NOT an error: ``matched_action=false`` and
    an empty ``surveys`` list. Does NOT create Display rows (the widget records each SHOWN survey
    via POST .../app-surveys/<survey_id>/displays — WidgetDisplayView). Rate-limited per IP + per env.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [WidgetTrackThrottle]

    def post(self, request, environment_id):
        environment = _get_environment_or_404(environment_id)
        serializer = WidgetTrackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        contact, action_class, surveys = public_services.track_action(
            environment=environment,
            user_id=payload["userId"],
            action=payload["action"],
            attributes=payload.get("attributes") or {},
            url=payload.get("url") or "",
        )
        return DRFResponse(
            {
                "userId": payload["userId"],
                "person_id": str(contact.id),
                "action": payload["action"],
                "matched_action": action_class is not None,
                "surveys": WidgetAppSurveySerializer(surveys, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class WidgetAppSurveysView(APIView):
    """GET the published app-surveys eligible to show this user right now (no action context).

    Path:   GET /api/v1/<org_slug>/public/environments/<env_id>/app-surveys?userId=<id>
    Returns 200: {
              "userId": "<echoed>",
              "person_id": "<resolved Contact uuid>",
              "surveys": [
                 {"id","name","survey_type","triggers","display_options"}, ...
              ]
            }

    This is the "catalog" the widget loads on init: every PUBLISHED app survey in the env that
    passes the per-user gates (displayLimit / recontact cooldown / displayPercentage) — but
    WITHOUT the trigger filter (the widget hasn't seen an action yet; it fires the returned
    surveys' triggers client-side via pageview/click/code matching). Each item carries ``triggers``
    + ``display_options`` so the widget knows WHEN/HOW to fire. ``userId`` is required (we need a
    person to evaluate the gates); a missing userId → 400. Rate-limited per IP + per env.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [WidgetAppSurveysThrottle]

    def get(self, request, environment_id):
        environment = _get_environment_or_404(environment_id)
        user_id = request.query_params.get("userId", "").strip()
        if not user_id:
            return DRFResponse(
                {"detail": "userId query parameter is required.", "code": "missing_user_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contact = public_services.get_or_create_person(environment, user_id)
        surveys = public_services.eligible_app_surveys(
            environment=environment, contact=contact, action_class=None
        )
        return DRFResponse(
            {
                "userId": user_id,
                "person_id": str(contact.id),
                "surveys": WidgetAppSurveySerializer(surveys, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class WidgetDisplayView(APIView):
    """POST record an app-survey impression (Display) shown to a userId (FEATURE_PARITY WS-11).

    Path:   POST /api/v1/<org_slug>/public/environments/<env_id>/app-surveys/<survey_id>/displays
    Body:   {"userId": "<client-supplied external user id>"}   # required
    Returns 201: {"display_id": "<uuid>", "person_id": "<Contact uuid>"}

    WHY this exists instead of reusing POST /public/surveys/{id}/displays: that endpoint derives
    the Display's contact_id from a ``link_token`` ONLY (the widget has none — it has a userId), so
    an app-survey impression there would be ANONYMOUS and the per-user displayLimit + recontact
    cooldown (both keyed on contact_id) would NEVER fire. Here we resolve userId → Contact (the
    SAME identity as track/app-surveys) and stamp the Display accordingly, so the matching counters
    work. Reuses the EXISTING Display model + funnel; only the contact-resolution source differs.

    The widget calls this AFTER it decides to actually SHOW a survey returned by track/app-surveys
    (so displayLimit counts shows, not returns). A non-published / non-app / foreign survey → 404
    (no impression). Submission still reuses the EXISTING public response create/patch/finalize loop.
    Rate-limited per IP + per env.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [WidgetDisplayThrottle]

    def post(self, request, environment_id, survey_id):
        environment = _get_environment_or_404(environment_id)
        serializer = WidgetDisplaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data["userId"]
        try:
            display = public_services.record_app_display(
                environment=environment, survey_id=survey_id, user_id=user_id
            )
        except public_services.AppSurveyNotFound:
            return DRFResponse(
                {"detail": "App survey not found.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return DRFResponse(
            {"display_id": str(display.id), "person_id": str(display.contact_id)},
            status=status.HTTP_201_CREATED,
        )
