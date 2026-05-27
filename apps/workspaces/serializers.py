"""
DRF serializers for Workspaces + Environments (contracts/openapi.yaml — Workspaces tag).

Response shapes mirror the openapi components:
  - ``Workspace`` = {id, name, slug, effective_access?, created_at}
  - ``Environment`` = {id, workspace_id, type, created_at}

``effective_access`` is the CALLER's effective access on the workspace (rbac.md §4), a
convenience field for UI gating. It is computed NOT stored. To avoid an N+1 in list
endpoints (rbac.md §7 "O(1) queries"), the view passes a precomputed
``{workspace_id: access}`` map via serializer context (``access_map``); the field reads
from that cache and never re-queries.
"""
from __future__ import annotations

from rest_framework import serializers

from .models import Environment, Workspace


class WorkspaceSerializer(serializers.ModelSerializer):
    """openapi ``Workspace``. ``effective_access`` is the caller's level (from context map)."""

    effective_access = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = [
            "id",
            "name",
            "slug",
            # General config (recontact cooldown) + enterprise white-label toggle. Read-only on
            # this serializer; mutated via the HTMX settings page (services.update_workspace_general).
            # WorkspaceUpdateSerializer stays name-only by design (slug + these are not PATCH-able
            # there yet — UI is the single write surface in this slice).
            "recontact_days",
            "remove_branding",
            "effective_access",
            "created_at",
        ]
        read_only_fields = fields

    def get_effective_access(self, obj):
        """Read the caller's access from the view-supplied ``access_map`` (no per-row query).

        Falls back to computing once for single-object (detail) responses where the view did
        not build a map. Returns None if unknown (should not happen for objects the caller can
        see, since they're filtered to readable workspaces).
        """
        access_map = self.context.get("access_map")
        if access_map is not None:
            return access_map.get(obj.id)
        # Detail fallback: compute for this one workspace (O(1) — single object).
        request = self.context.get("request")
        if request is None:
            return None
        from apps.common.permissions import effective_access

        return effective_access(request.user, obj)


class WorkspaceCreateSerializer(serializers.Serializer):
    """POST /api/v1/{org_slug}/workspaces — {name, slug?}. slug auto-derived if omitted."""

    name = serializers.CharField(max_length=255)
    slug = serializers.SlugField(max_length=255, required=False, allow_blank=True)


class WorkspaceUpdateSerializer(serializers.Serializer):
    """PATCH /api/v1/{org_slug}/workspaces/{id} — only ``name`` is mutable (slug immutable,
    mirrors org slug immutability — public/admin links embed it)."""

    name = serializers.CharField(max_length=255)


class EnvironmentSerializer(serializers.ModelSerializer):
    """openapi ``Environment`` = {id, workspace_id, type, created_at}."""

    workspace_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Environment
        fields = ["id", "workspace_id", "type", "created_at"]
        read_only_fields = fields
