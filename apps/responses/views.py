"""
DRF API views for the Response READ side (contracts/openapi.yaml — Responses tag, read only).

SCOPE (Wave 2c brief): this implements ONLY the authenticated admin/builder/analytics READ
path — list responses for a survey + get a single response. The public submission path
(POST/PATCH/finalize via token, rate-limiting) is the RUNTIME wave and is intentionally NOT
here. Likewise the authenticated WRITE actions (delete response, tag response, export) are out
of this wave's scope.

All endpoints are TENANT-prefixed (mounted at ``api/v1/...`` after TenantSlugMiddleware strips
the org slug). ``IsOrgMember`` gates membership; authorization delegates to
``apps.common.permissions.can()`` (rbac.md §7). Both endpoints require ``view_response``
(workspace ``read``). A response/survey the caller cannot see returns 404 (no existence oracle),
mirroring the surveys + workspaces views.

openapi operationIds (Responses tag — read side; mapped by path+method):
  GET /environments/.../surveys/{survey_id}/responses → ResponseListView.get   (view_response, scoped to survey)
  GET /responses/{response_id}                        → ResponseDetailView.get (view_response)
"""
from __future__ import annotations

from django.http import Http404
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView

from apps.common.permissions import IsOrgMember, can
from apps.organizations.pagination import OpenapiPageNumberPagination
from apps.surveys.models import Survey

from .models import Response
from .serializers import ResponseSerializer


def _get_survey_or_404(survey_id) -> Survey:
    try:
        return Survey.objects.select_related("environment", "environment__workspace").get(
            pk=survey_id
        )
    except (Survey.DoesNotExist, ValueError) as exc:
        raise Http404("Survey not found.") from exc


def _get_response_or_404(response_id) -> Response:
    try:
        return (
            Response.objects.select_related(
                "survey", "survey__environment", "survey__environment__workspace"
            )
            .prefetch_related("response_tags__tag")
            .get(pk=response_id)
        )
    except (Response.DoesNotExist, ValueError) as exc:
        raise Http404("Response not found.") from exc


class ResponseListView(APIView):
    """GET responses for a survey (read). openapi: /surveys/{survey_id}/responses.

    Scoped to one survey via the path; the caller must be able to view the survey
    (``view_response`` == workspace ``read``, same level as ``view_survey``). Filters:
    ``finished`` (bool), ``created_after`` / ``created_before`` (ISO date-time), plus
    ``page`` / ``page_size``. ``segment_id`` is accepted by the openapi but its evaluation
    is analytics territory and is NOT implemented in this wave (→ 400; see the view).
    """

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_response"}

    def get(self, request, survey_id):
        survey = _get_survey_or_404(survey_id)
        # view_response is workspace `read`. Invisible survey → 404 (no existence oracle).
        if not can(request.user, "view_response", survey):
            raise Http404("Survey not found.")

        # segment_id filtering requires the segment-filter→contact→response evaluator, which is
        # analytics-wave territory (the Segment.filter boolean tree over ContactAttribute rows).
        # We do NOT silently mis-filter: reject with a clear 400 so callers don't assume it works.
        if request.query_params.get("segment_id"):
            return DRFResponse(
                {
                    "detail": "Filtering responses by segment_id is not implemented in this "
                    "wave (it belongs to the analytics path).",
                    "code": "segment_filter_unsupported",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        responses = (
            Response.objects.filter(survey=survey)
            .prefetch_related("response_tags__tag")
            .order_by("-created_at", "id")
        )

        finished = request.query_params.get("finished")
        if finished is not None:
            responses = responses.filter(finished=_parse_bool(finished))

        created_after = request.query_params.get("created_after")
        if created_after:
            dt = _parse_dt(created_after)
            if dt is not None:
                responses = responses.filter(created_at__gte=dt)

        created_before = request.query_params.get("created_before")
        if created_before:
            dt = _parse_dt(created_before)
            if dt is not None:
                responses = responses.filter(created_at__lte=dt)

        paginator = OpenapiPageNumberPagination()
        page = paginator.paginate_queryset(responses, request, view=self)
        return paginator.get_paginated_response(
            ResponseSerializer(page, many=True).data
        )


class ResponseDetailView(APIView):
    """GET a single response (read). openapi: /responses/{response_id}.

    Requires ``view_response`` on the response's workspace (via response.survey.environment.
    workspace — resolved by ``workspace_of()``). Invisible → 404.
    """

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_response"}

    def get(self, request, response_id):
        response = _get_response_or_404(response_id)
        if not can(request.user, "view_response", response):
            raise Http404("Response not found.")
        return DRFResponse(ResponseSerializer(response).data)


def _parse_bool(raw: str) -> bool:
    """Lenient query-param boolean: 'true'/'1'/'yes' → True (case-insensitive)."""
    return str(raw).strip().lower() in ("true", "1", "yes")


def _parse_dt(raw: str):
    """Parse an ISO-8601 date-time query param, tolerant of an unencoded '+' offset.

    A client that does not URL-encode an ISO datetime sends the ``+HH:MM`` offset as a literal
    space (``%2B`` would be the encoded form), which ``parse_datetime`` rejects. A literal space
    at that position can only mean a ``+`` in an ISO datetime, so we retry with that repair.
    Returns ``None`` if still unparseable (the filter is then skipped — lenient query handling).
    """
    dt = parse_datetime(raw)
    if dt is not None:
        return dt
    return parse_datetime(raw.replace(" ", "+"))
