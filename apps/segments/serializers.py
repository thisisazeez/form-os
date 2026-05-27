"""
DRF serializers for Segments (contracts/openapi.yaml — Segments tag).

Response shapes mirror the openapi components:
  - ``Segment``      = {id, environment_id, title, filter, created_at}
  - ``SegmentWrite`` = {title, environment_id, filter}   (POST; environment_id required)
  - PUT replace uses ``SegmentReplaceWrite`` = {title, filter} (environment is immutable —
    taken from the existing segment; the URL already identifies it).

The ``filter`` JSON is validated in the SERVICE layer (``segments.services.
validate_segment_filter`` — structural jsonschema + semantic operator/type), not here, because
the semantic layer needs the environment's attribute keys. The serializer enforces only that
``filter`` is a JSON object.
"""
from __future__ import annotations

from rest_framework import serializers

from .models import Segment


class SegmentSerializer(serializers.ModelSerializer):
    """openapi ``Segment`` = {id, environment_id, title, filter, created_at}."""

    environment_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Segment
        fields = ["id", "environment_id", "title", "filter", "created_at"]
        read_only_fields = fields


class SegmentCreateSerializer(serializers.Serializer):
    """openapi ``SegmentWrite`` — {title, environment_id, filter}. environment_id pins the env
    the segment (and its filter's attribute keys) scope to."""

    title = serializers.CharField(max_length=255)
    environment_id = serializers.UUIDField()
    filter = serializers.JSONField()

    def validate_filter(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("filter must be a JSON object.")
        return value


class SegmentReplaceSerializer(serializers.Serializer):
    """PUT replace — {title, filter}. Environment is immutable (identified by the URL)."""

    title = serializers.CharField(max_length=255)
    filter = serializers.JSONField()

    def validate_filter(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("filter must be a JSON object.")
        return value


class SegmentPreviewSerializer(serializers.Serializer):
    """[ADD] POST preview — {filter} for an unsaved filter (builder count/preview)."""

    filter = serializers.JSONField()

    def validate_filter(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("filter must be a JSON object.")
        return value
