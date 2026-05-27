"""
PUBLIC survey-definition GET (openapi "Public Survey" tag — the survey-read endpoint).

This is the unauthenticated, rate-limited endpoint the runtime hits to fetch the
``PublicSurvey`` shape for a PUBLISHED survey. It is the ONLY public-read view in
``apps/surveys`` — the authenticated CRUD in ``views.py`` is untouched.

operationId mapping (openapi paths; the spec uses summaries not explicit operationIds):
  GET /api/v1/{org_slug}/public/surveys/{survey_id}
      → PublicSurveyView.get  (security: []; x-rate-limit per-IP per-survey)

Behavior (frozen by openapi):
  - 200 + PublicSurvey      — survey is ``published`` (and link, if required/given, is usable)
  - 404                     — unknown survey OR not published (no draft/paused/closed leak,
                              no status oracle)
  - 410                     — single-use link consumed/expired, or single_use survey with no
                              link_token (resolve_link's LinkRequiredError/LinkGoneError)
  - 429                     — rate limit (the throttle class)

The link is honored on READ so a consumed single-use link shows "gone" before the runtime
renders the form (the same resolution the create endpoint uses — one source of truth in
``public_services``). No auth; the response carries only the public allow-list shape.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView

from apps.responses import public_services
from apps.responses.public_services import LinkGoneError, LinkRequiredError
from apps.responses.throttling import PublicSurveyReadThrottle

from .public_serializers import PublicSurveySerializer


class PublicSurveyView(APIView):
    """GET the published survey definition for the runtime (openapi PublicSurvey).

    Unauthenticated (``security: []``): no SessionAuth (which also disables DRF CSRF
    enforcement), ``AllowAny``, and a per-IP/per-survey throttle.
    """

    authentication_classes: list = []  # public — no SessionAuth (also no CSRF enforcement)
    permission_classes = [AllowAny]
    throttle_classes = [PublicSurveyReadThrottle]

    def get(self, request, survey_id):
        survey = public_services.get_published_survey(survey_id)
        if survey is None:
            # Unknown OR not published — uniform 404 (no draft/paused/closed leak).
            return DRFResponse(
                {"detail": "Survey not found.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Honor the link on read: a consumed/expired single-use link → 410; a single_use
        # survey with no link_token → 410 ("needs a link"). resolve_link is the single
        # source of this logic (shared with the create path).
        link_token = request.query_params.get("link_token")
        try:
            public_services.resolve_link(survey, link_token)
        except (LinkGoneError, LinkRequiredError) as exc:
            return DRFResponse(
                {"detail": str(exc), "code": "link_gone"},
                status=status.HTTP_410_GONE,
            )

        return DRFResponse(PublicSurveySerializer(survey).data)
