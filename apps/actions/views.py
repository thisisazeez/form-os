"""
DRF API views for the ActionClass management API (FEATURE_PARITY WS-11 "Actions / Events").

TENANT-prefixed (mounted at ``api/v1/...`` after TenantSlugMiddleware strips the org slug —
config/urls.py). Mirrors the Contacts/Surveys views EXACTLY:
  - ``IsOrgMember`` gates membership; authorization delegates to ``apps.common.permissions.can()``
    (rbac.md §7 — the single entry point). ``permission_action_map`` declares the
    action→permission mapping (auditable, rbac.md §8.1).
  - ActionClasses are workspace-scoped via ``.environment.workspace`` (workspace_of() resolves
    it), so views load with ``select_related("environment__workspace")`` and pass the instance
    to ``can()`` (no extra query inside the object check).
  - LIST endpoints FILTER, not 403 (rbac.md §8.1): list returns only the env's actions when the
    caller can ``read`` that env's workspace, else an empty list (no existence oracle).
  - DETAIL reads on an action the caller cannot see return 404 (no existence oracle).

RBAC actions (apps/common/permissions.py REQUIRED): view_action_class (ws read),
create/edit/delete_action_class (ws write — mutating actions are workspace `write`, mirroring
surveys' create/edit).

Endpoints (the spec uses summaries; mapped here by path+method):
  GET    /environments/{env_id}/actions      → ActionClassListCreateView.get   (view_action_class, filtered)
  POST   /environments/{env_id}/actions      → ActionClassListCreateView.post  (create_action_class)
  GET    /actions/{id}                         → ActionClassDetailView.get       (view_action_class)
  PATCH  /actions/{id}                         → ActionClassDetailView.patch     (edit_action_class)
  DELETE /actions/{id}                         → ActionClassDetailView.delete    (delete_action_class)
"""
from __future__ import annotations

from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsOrgMember, can
from apps.contacts.scoping import get_environment_or_404

from . import services
from .models import ActionClass
from .serializers import (
    ActionClassSerializer,
    ActionClassUpdateSerializer,
    ActionClassWriteSerializer,
)


def _get_action_or_404(action_id) -> ActionClass:
    try:
        return ActionClass.objects.select_related("environment__workspace").get(pk=action_id)
    except (ActionClass.DoesNotExist, ValueError):
        raise Http404("Action not found.") from None


class ActionClassListCreateView(APIView):
    """GET (list, filtered) / POST (create) action classes in an environment."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_action_class", "post": "create_action_class"}

    def get(self, request, environment_id):
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "view_action_class", environment):
            # Caller cannot read this workspace → empty list (default-deny; not a 403).
            return Response([])
        actions = ActionClass.objects.filter(environment=environment).order_by("name", "id")
        return Response(ActionClassSerializer(actions, many=True).data)

    def post(self, request, environment_id):
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "create_action_class", environment):
            return Response(
                {"detail": "You do not have permission to create actions here."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ActionClassWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            action = services.create_action_class(
                environment=environment,
                name=data["name"],
                action_type=data["type"],
                description=data.get("description", ""),
                config=data.get("config"),
                created_by_user_id=request.user.id,
            )
        except services.ActionConfigError as exc:
            return Response(
                {"detail": "Invalid action config.", "errors": exc.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(
                {"detail": "An action with this name already exists in this environment."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(ActionClassSerializer(action).data, status=status.HTTP_201_CREATED)


class ActionClassDetailView(APIView):
    """GET (read) / PATCH (edit) / DELETE an action class."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {
        "get": "view_action_class",
        "patch": "edit_action_class",
        "delete": "delete_action_class",
    }

    def get(self, request, action_id):
        action = _get_action_or_404(action_id)
        if not can(request.user, "view_action_class", action):
            raise Http404("Action not found.")  # no existence oracle.
        return Response(ActionClassSerializer(action).data)

    def patch(self, request, action_id):
        action = _get_action_or_404(action_id)
        if not can(request.user, "edit_action_class", action):
            return Response(
                {"detail": "You do not have permission to edit this action."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ActionClassUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            services.update_action_class(
                action_class=action,
                name=data.get("name"),
                action_type=data.get("type"),
                description=data.get("description"),
                config=data["config"] if "config" in data else "__unset__",
            )
        except services.ActionConfigError as exc:
            return Response(
                {"detail": "Invalid action config.", "errors": exc.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(
                {"detail": "An action with this name already exists in this environment."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(ActionClassSerializer(action).data)

    def delete(self, request, action_id):
        action = _get_action_or_404(action_id)
        if not can(request.user, "delete_action_class", action):
            return Response(
                {"detail": "You do not have permission to delete this action."},
                status=status.HTTP_403_FORBIDDEN,
            )
        services.delete_action_class(action_class=action)
        return Response(status=status.HTTP_204_NO_CONTENT)
