"""
DRF API views for Surveys + lifecycle (contracts/openapi.yaml — Surveys tag).

All endpoints are TENANT-prefixed (mounted at ``api/v1/...`` after TenantSlugMiddleware strips
the org slug — config/urls.py). ``IsOrgMember`` gates membership; authorization delegates to
``apps.common.permissions.can()`` (rbac.md §7) — the single entry point. ``permission_action_map``
on each view declares the action→permission mapping so it is auditable (rbac.md §8.1).

LIST endpoints FILTER, not 403 (rbac.md §8.1): the survey list under an environment is scoped to
that environment AND filtered to workspaces the caller can ``read`` (reusing the N+1-free
``workspace_access_map`` from apps/workspaces/access.py — NO per-row ``can()``). Detail reads on a
survey the caller cannot see return 404 (no existence oracle), mirroring the workspaces views.

openapi operationIds (Surveys tag — the spec uses summaries, not explicit operationIds; mapped
here by path+method):
  GET    /environments/{environment_id}/surveys → SurveyListCreateView.get   (view_survey, filtered)
  POST   /environments/{environment_id}/surveys → SurveyListCreateView.post  (create_survey)
  GET    /surveys/{survey_id}                   → SurveyDetailView.get        (view_survey)
  PUT    /surveys/{survey_id}                   → SurveyDetailView.put        (edit_survey)
  DELETE /surveys/{survey_id}                   → SurveyDetailView.delete     (delete_survey)
  POST   /surveys/{survey_id}/status            → SurveyStatusView.post       (publish/pause/close_survey)
"""
from __future__ import annotations

from django.db.models import Count
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.common.permissions import IsOrgMember, can
from apps.organizations.pagination import OpenapiPageNumberPagination
from apps.workspaces.access import workspace_access_map
from apps.workspaces.models import Environment

from . import services
from .models import Survey
from .serializers import (
    SurveySerializer,
    SurveyStatusUpdateSerializer,
    SurveySummarySerializer,
    SurveyWriteSerializer,
)
from .validators import SurveyValidationError


def _validation_error_body(exc: SurveyValidationError) -> dict:
    """Map ``SurveyValidationError`` to the openapi ``ValidationErrorBody`` shape:
    ``{detail, schema_errors?, errors?}``. ``schema_errors`` carries jsonschema messages;
    ``errors.logic`` carries the in-code ``validate_survey`` error dicts."""
    body: dict = {"detail": exc.message}
    if exc.schema_errors:
        body["schema_errors"] = exc.schema_errors
    if exc.logic_errors:
        body["errors"] = {"logic": exc.logic_errors}
    return body


def _get_environment_or_404(environment_id) -> Environment:
    try:
        return Environment.objects.select_related("workspace").get(pk=environment_id)
    except (Environment.DoesNotExist, ValueError) as exc:
        raise Http404("Environment not found.") from exc


def _get_survey_or_404(survey_id) -> Survey:
    try:
        return Survey.objects.select_related("environment", "environment__workspace").get(
            pk=survey_id
        )
    except (Survey.DoesNotExist, ValueError) as exc:
        raise Http404("Survey not found.") from exc


class SurveyListCreateView(APIView):
    """GET (list within an environment, filtered) / POST (create) surveys.

    openapi: /api/v1/{org_slug}/environments/{environment_id}/surveys
    """

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_survey", "post": "create_survey"}

    def get(self, request, environment_id):
        environment = _get_environment_or_404(environment_id)
        # rbac.md §8.1: filter to readable workspaces. Build the {ws_id: access} map ONCE;
        # if the caller can't read this environment's workspace, the list is empty (no oracle).
        access_map = workspace_access_map(request.user)
        if environment.workspace_id not in access_map:
            # Caller cannot read this workspace → empty page (default-deny; not a 403).
            paginator = OpenapiPageNumberPagination()
            page = paginator.paginate_queryset(Survey.objects.none(), request, view=self)
            return paginator.get_paginated_response(
                SurveySummarySerializer(page, many=True).data
            )

        surveys = (
            Survey.objects.filter(environment=environment)
            .annotate(response_count=Count("responses"))
            .order_by("-created_at", "id")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            surveys = surveys.filter(status=status_filter)

        paginator = OpenapiPageNumberPagination()
        page = paginator.paginate_queryset(surveys, request, view=self)
        return paginator.get_paginated_response(
            SurveySummarySerializer(page, many=True).data
        )

    def post(self, request, environment_id):
        environment = _get_environment_or_404(environment_id)
        # create_survey requires workspace `write` on this environment's workspace.
        if not can(request.user, "create_survey", environment):
            return Response(
                {"detail": "You do not have permission to create surveys here."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SurveyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Path env is the source of truth; reject a conflicting body environment_id (400).
        body_env = data.get("environment_id")
        if body_env is not None and str(body_env) != str(environment_id):
            return Response(
                {
                    "detail": "environment_id in body does not match the path environment.",
                    "errors": {"environment_id": ["Does not match the path environment."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            survey = services.create_survey(
                environment=environment,
                name=data["name"],
                questions=data["questions"],
                languages=data["languages"],
                logic=data.get("logic"),
                styling=data.get("styling"),
                welcome_card=data.get("welcome_card"),
                endings=data.get("endings"),
                close_on=data.get("close_on"),
                hidden_fields=data.get("hidden_fields"),
                follow_ups=data.get("follow_ups"),
                blocks=data.get("blocks"),
                variables=data.get("variables"),
                quotas=data.get("quotas"),
                survey_type=data.get("survey_type"),
                triggers=data.get("triggers"),
                display_options=data.get("display_options"),
                single_use=data.get("single_use", False),
                capture_ip=data.get("capture_ip", False),
                created_by_user_id=request.user.id,
            )
        except SurveyValidationError as exc:
            return Response(
                _validation_error_body(exc), status=status.HTTP_400_BAD_REQUEST
            )
        audit.record_from_request(
            request,
            action=audit.ACTION_SURVEY_CREATED,
            target_type="survey",
            target_id=survey.id,
            metadata={"name": survey.name},
        )
        return Response(
            SurveySerializer(survey).data, status=status.HTTP_201_CREATED
        )


class SurveyDetailView(APIView):
    """GET (read) / PUT (replace) / DELETE a survey.

    openapi: /api/v1/{org_slug}/surveys/{survey_id}
    """

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {
        "get": "view_survey",
        "put": "edit_survey",
        "delete": "delete_survey",
    }

    def get(self, request, survey_id):
        survey = _get_survey_or_404(survey_id)
        if not can(request.user, "view_survey", survey):
            # Not visible to the caller → 404 (no existence oracle; rbac.md NotFound semantics).
            raise Http404("Survey not found.")
        return Response(SurveySerializer(survey).data)

    def put(self, request, survey_id):
        survey = _get_survey_or_404(survey_id)
        if not can(request.user, "edit_survey", survey):
            return Response(
                {"detail": "You do not have permission to edit this survey."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SurveyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            survey = services.replace_survey(
                survey=survey,
                name=data["name"],
                questions=data["questions"],
                languages=data["languages"],
                logic=data.get("logic"),
                styling=data.get("styling"),
                welcome_card=data.get("welcome_card"),
                endings=data.get("endings"),
                close_on=data.get("close_on"),
                hidden_fields=data.get("hidden_fields"),
                follow_ups=data.get("follow_ups"),
                blocks=data.get("blocks"),
                variables=data.get("variables"),
                quotas=data.get("quotas"),
                survey_type=data.get("survey_type"),
                triggers=data.get("triggers"),
                display_options=data.get("display_options"),
                single_use=data.get("single_use"),
                capture_ip=data.get("capture_ip"),
            )
        except SurveyValidationError as exc:
            return Response(
                _validation_error_body(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(SurveySerializer(survey).data)

    def delete(self, request, survey_id):
        survey = _get_survey_or_404(survey_id)
        # delete_survey requires workspace `manage` (rbac.md §6).
        if not can(request.user, "delete_survey", survey):
            return Response(
                {"detail": "You do not have permission to delete this survey."},
                status=status.HTTP_403_FORBIDDEN,
            )
        survey_name = survey.name
        services.delete_survey(survey=survey)
        audit.record_from_request(
            request,
            action=audit.ACTION_SURVEY_DELETED,
            target_type="survey",
            target_id=survey_id,
            metadata={"name": survey_name},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SurveyStatusView(APIView):
    """POST a status transition (publish/pause/close). Enforces valid transitions.

    openapi: /api/v1/{org_slug}/surveys/{survey_id}/status

    Per the openapi ``x-permission-note``: the rbac action is dispatched on the TARGET status
    (published -> publish_survey, paused -> pause_survey, closed -> close_survey). All three are
    workspace ``write`` (rbac.md §6).
    """

    permission_classes = [IsAuthenticated, IsOrgMember]
    # All transition targets are workspace `write`; the representative is publish_survey.
    permission_action_map = {"post": "publish_survey"}

    def post(self, request, survey_id):
        survey = _get_survey_or_404(survey_id)
        serializer = SurveyStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_status = serializer.validated_data["status"]

        # Dispatch the rbac action on the TARGET status (x-permission-note). An invalid target
        # (e.g. "draft", which has no status-endpoint edge) → 400 before any permission/DB work.
        try:
            rbac_action = services.action_for_status(target_status)
        except services.InvalidStatusTransition as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if not can(request.user, rbac_action, survey):
            return Response(
                {"detail": f"You do not have permission to {rbac_action.replace('_', ' ')}."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            survey = services.transition_status(survey=survey, target_status=target_status)
        except services.InvalidStatusTransition as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # Audit publish/close (the two transitions with a webhook event + lifecycle weight).
        _status_audit_action = {
            "published": audit.ACTION_SURVEY_PUBLISHED,
            "closed": audit.ACTION_SURVEY_CLOSED,
        }.get(target_status)
        if _status_audit_action is not None:
            audit.record_from_request(
                request,
                action=_status_audit_action,
                target_type="survey",
                target_id=survey.id,
                metadata={"name": survey.name, "status": survey.status},
            )
        return Response(SurveySerializer(survey).data)
