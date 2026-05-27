"""
DRF API views for Workspaces + Environments (contracts/openapi.yaml — Workspaces tag).

All endpoints are TENANT-prefixed (mounted at ``api/v1/...`` after TenantSlugMiddleware strips
the org slug — config/urls.py). The routed org is ``request.tenant``; ``IsOrgMember`` gates
membership and caches ``request.org_membership`` / ``request.organization``.

Authorization delegates to ``apps.common.permissions.can()`` (rbac.md §7) — the single entry
point. The ``permission_action_map`` on each view declares the action→permission mapping so
it is auditable (rbac.md §8.1).

LIST endpoints FILTER, not 403 (rbac.md §8.1): ``GET /workspaces`` returns only workspaces the
caller can ``read``, computed via ONE precomputed ``{workspace_id: access}`` map (NO per-row
``can()`` — rbac.md §7 "O(1) queries"). The map is injected into the serializer context so the
``effective_access`` field reads from cache.

openapi operationIds (Workspaces tag — the spec uses summaries, not explicit operationIds;
mapped here by path+method):
  GET    /workspaces                          → WorkspaceListCreateView.get      (view_workspace, filtered)
  POST   /workspaces                          → WorkspaceListCreateView.post     (create_workspace)
  GET    /workspaces/{id}                     → WorkspaceDetailView.get          (view_workspace)
  PATCH  /workspaces/{id}                     → WorkspaceDetailView.patch        (manage_workspace)
  DELETE /workspaces/{id}                     → WorkspaceDetailView.delete       (delete_workspace)
  GET    /workspaces/{id}/environments        → WorkspaceEnvironmentsView.get    (view_workspace)
"""
from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsOrgMember, can, effective_access

from . import services
from .access import readable_workspaces_with_access
from .models import Workspace
from .serializers import (
    EnvironmentSerializer,
    WorkspaceCreateSerializer,
    WorkspaceSerializer,
    WorkspaceUpdateSerializer,
)


def _errors(exc: DjangoValidationError) -> dict:
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"detail": exc.messages[0] if exc.messages else "Invalid request."}


def _get_workspace_or_404(workspace_id) -> Workspace:
    try:
        return Workspace.objects.get(pk=workspace_id)
    except (Workspace.DoesNotExist, ValueError, DjangoValidationError) as exc:
        raise Http404("Workspace not found.") from exc


class WorkspaceListCreateView(APIView):
    """GET (list, filtered) / POST (create) workspaces."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_workspace", "post": "create_workspace"}

    def get(self, request):
        # rbac.md §8.1: filter to readable workspaces; build the {id: access} map ONCE.
        workspaces, access_map = readable_workspaces_with_access(request.user)
        data = WorkspaceSerializer(
            workspaces, many=True, context={"request": request, "access_map": access_map}
        ).data
        return Response(data)

    def post(self, request):
        # Org-level gate (create_workspace = owner/admin). obj=None (collection action).
        if not can(request.user, "create_workspace", None):
            return Response(
                {"detail": "You do not have permission to create workspaces."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = WorkspaceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            workspace = services.create_workspace(
                name=serializer.validated_data["name"],
                slug=serializer.validated_data.get("slug"),
                created_by_user_id=request.user.id,
            )
        except services.SlugTakenError as exc:
            return Response(_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        # Creator's effective access (org owner/admin → manage) for the response convenience field.
        access_map = {workspace.id: effective_access(request.user, workspace)}
        return Response(
            WorkspaceSerializer(
                workspace, context={"request": request, "access_map": access_map}
            ).data,
            status=status.HTTP_201_CREATED,
        )


class WorkspaceDetailView(APIView):
    """GET (read) / PATCH (manage) / DELETE (org owner/admin) a workspace."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {
        "get": "view_workspace",
        "patch": "manage_workspace",
        "delete": "delete_workspace",
    }

    def get(self, request, workspace_id):
        workspace = _get_workspace_or_404(workspace_id)
        if not can(request.user, "view_workspace", workspace):
            # Not visible to the caller → 404 (no existence oracle; rbac.md NotFound semantics).
            raise Http404("Workspace not found.")
        return Response(
            WorkspaceSerializer(workspace, context={"request": request}).data
        )

    def patch(self, request, workspace_id):
        workspace = _get_workspace_or_404(workspace_id)
        if not can(request.user, "manage_workspace", workspace):
            return Response(
                {"detail": "You do not have permission to manage this workspace."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = WorkspaceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_workspace(
            workspace=workspace, name=serializer.validated_data["name"]
        )
        return Response(
            WorkspaceSerializer(workspace, context={"request": request}).data
        )

    def delete(self, request, workspace_id):
        workspace = _get_workspace_or_404(workspace_id)
        # delete_workspace is org owner/admin only (rbac.md §6; obj resolves the workspace but
        # the requirement is org-level — can() evaluates the membership role).
        if not can(request.user, "delete_workspace", workspace):
            return Response(
                {"detail": "You do not have permission to delete this workspace."},
                status=status.HTTP_403_FORBIDDEN,
            )
        workspace.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceEnvironmentsView(APIView):
    """GET the workspace's environments (production, development). Requires view_workspace."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_workspace"}

    def get(self, request, workspace_id):
        workspace = _get_workspace_or_404(workspace_id)
        if not can(request.user, "view_workspace", workspace):
            raise Http404("Workspace not found.")
        environments = workspace.environments.order_by("type")
        return Response(EnvironmentSerializer(environments, many=True).data)
