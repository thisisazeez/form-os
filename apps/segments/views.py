"""
DRF API views for Segments + segment evaluation (contracts/openapi.yaml — Segments tag).

TENANT-prefixed (mounted at ``api/v1/...`` after the slug is stripped — config/urls.py). The
routed org is ``request.tenant``; ``IsOrgMember`` gates membership.

Authorization delegates to ``apps.common.permissions.can()`` (rbac.md §7). Segments are
workspace-scoped via ``.environment.workspace`` (workspace_of() in permissions.py); views load
with ``select_related("environment__workspace")`` and pass the instance to ``can()``.

LIST FILTERS, not 403 (rbac.md §8.1): list segments returns only those in environments the
caller can ``read`` (via the shared ``readable_environment_ids`` helper). Detail GET on an
invisible segment → 404 (no existence oracle). Filter VALIDATION errors → 400 with
``schema_errors`` (openapi ValidationErrorBody).

openapi (Segments tag — mapped by path+method). [ADD] = brief-required additions not in
openapi.yaml (segment EVALUATION + unsaved-filter PREVIEW), flagged in the producer summary:
  GET    /environments/{env_id}/segments            → SegmentListCreateView.get  (view_segment, filtered)
  POST   /environments/{env_id}/segments            → SegmentListCreateView.post (create_segment)
  PUT    /segments/{id}                              → SegmentDetailView.put      (edit_segment; full replace)
  DELETE /segments/{id}                              → SegmentDetailView.delete   (delete_segment)
  GET    /segments/{id}/contacts [ADD]               → SegmentContactsView.get    (view_segment; EVALUATION)
  POST   /environments/{env_id}/segments/preview [ADD] → SegmentPreviewView.post  (view_segment; unsaved filter)
"""
from __future__ import annotations

from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsOrgMember, can
from apps.contacts.scoping import (
    get_environment_or_404,
    pii_unmasked_for_environment,
)
from apps.contacts.serializers import ContactSerializer
from apps.contacts.views import ContactsPagination

from . import services
from .models import Segment
from .serializers import (
    SegmentCreateSerializer,
    SegmentPreviewSerializer,
    SegmentReplaceSerializer,
    SegmentSerializer,
)

# Default cap on matched contacts returned/scanned by evaluation + preview (avoid OOM on a huge
# environment; the builder paginates contacts via the contact list endpoint for full browsing).
DEFAULT_EVAL_LIMIT = 1000


def _get_segment_or_404(segment_id) -> Segment:
    try:
        return Segment.objects.select_related("environment__workspace").get(pk=segment_id)
    except (Segment.DoesNotExist, ValueError):
        raise Http404("Segment not found.") from None


def _filter_error_response(exc: services.SegmentFilterError) -> Response:
    return Response(
        {"detail": "Invalid segment filter.", "schema_errors": exc.schema_errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


class SegmentListCreateView(APIView):
    """GET (list, filtered) / POST (create) segments in an environment."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_segment", "post": "create_segment"}

    def get(self, request, environment_id):
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "view_segment", environment):
            raise Http404("Environment not found.")
        segments = Segment.objects.filter(environment=environment).order_by("title", "id")
        return Response(SegmentSerializer(segments, many=True).data)

    def post(self, request, environment_id):
        # environment_id comes from the URL (what RBAC scopes to). openapi SegmentWrite also
        # carries it in the body; we REQUIRE the body value to MATCH the URL (reject a mismatch
        # with 400 rather than silently using one over the other — no ambiguity about where the
        # segment landed).
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "create_segment", environment):
            return Response(
                {"detail": "You do not have permission to create segments here."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SegmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body_env_id = serializer.validated_data["environment_id"]
        if str(body_env_id) != str(environment_id):
            return Response(
                {
                    "detail": "Body environment_id does not match the URL environment.",
                    "errors": {"environment_id": ["Must match the URL environment."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            segment = services.create_segment(
                environment=environment,
                title=serializer.validated_data["title"],
                filter_def=serializer.validated_data["filter"],
                created_by_user_id=request.user.id,
            )
        except services.SegmentFilterError as exc:
            return _filter_error_response(exc)
        except services.SegmentTitleTakenError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SegmentSerializer(segment).data, status=status.HTTP_201_CREATED)


class SegmentDetailView(APIView):
    """PUT (replace) / DELETE a segment. (openapi has no GET-detail for segments — the list
    endpoint covers read.) Mutation denial returns 403 (matches WorkspaceDetailView /
    ContactDetailView precedent)."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"put": "edit_segment", "delete": "delete_segment"}

    def put(self, request, segment_id):
        segment = _get_segment_or_404(segment_id)
        if not can(request.user, "edit_segment", segment):
            # 403 (matches the codebase precedent — WorkspaceDetailView / ContactDetailView).
            # Existence-oracle hardening is a codebase-wide concern, not Wave 2c's job.
            return Response(
                {"detail": "You do not have permission to edit this segment."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SegmentReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.replace_segment(
                segment=segment,
                title=serializer.validated_data["title"],
                filter_def=serializer.validated_data["filter"],
            )
        except services.SegmentFilterError as exc:
            return _filter_error_response(exc)
        except services.SegmentTitleTakenError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SegmentSerializer(segment).data)

    def delete(self, request, segment_id):
        segment = _get_segment_or_404(segment_id)
        if not can(request.user, "delete_segment", segment):
            return Response(
                {"detail": "You do not have permission to delete this segment."},
                status=status.HTTP_403_FORBIDDEN,
            )
        segment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SegmentContactsView(APIView):
    """[ADD] GET the contacts matching a saved segment (segment EVALUATION). Requires
    view_segment (which implies view_contact — both need workspace read on the same env).
    Paginated; PII masked for read-only callers. ``?limit=`` caps the scan (default 1000)."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_segment"}

    def get(self, request, segment_id):
        segment = _get_segment_or_404(segment_id)
        if not can(request.user, "view_segment", segment):
            raise Http404("Segment not found.")
        limit = _parse_limit(request)
        matched = services.evaluate(segment, limit=limit)
        unmasked = pii_unmasked_for_environment(request.user, segment.environment)
        paginator = ContactsPagination()
        page = paginator.paginate_queryset(matched, request, view=self)
        data = ContactSerializer(
            page, many=True, context={"request": request, "pii_unmasked": unmasked}
        ).data
        return paginator.get_paginated_response(data)


class SegmentPreviewView(APIView):
    """[ADD] POST an UNSAVED filter to count/preview matching contacts (builder support).
    Returns {count, results} — results capped by ``?limit=`` (default 1000). view_segment."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"post": "view_segment"}

    def post(self, request, environment_id):
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "view_segment", environment):
            raise Http404("Environment not found.")
        serializer = SegmentPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        limit = _parse_limit(request)
        try:
            matched = services.preview(
                serializer.validated_data["filter"], environment=environment, limit=limit
            )
        except services.SegmentFilterError as exc:
            return _filter_error_response(exc)
        unmasked = pii_unmasked_for_environment(request.user, environment)
        data = ContactSerializer(
            matched, many=True, context={"request": request, "pii_unmasked": unmasked}
        ).data
        return Response({"count": len(matched), "results": data})


def _parse_limit(request) -> int:
    """Parse ``?limit=`` (positive int, capped at DEFAULT_EVAL_LIMIT). Default = the cap."""
    raw = request.query_params.get("limit")
    if raw is None:
        return DEFAULT_EVAL_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_EVAL_LIMIT
    if value <= 0:
        return DEFAULT_EVAL_LIMIT
    return min(value, DEFAULT_EVAL_LIMIT)
