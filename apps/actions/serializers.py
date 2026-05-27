"""
DRF serializers for the ActionClass management API (FEATURE_PARITY WS-11 "Actions / Events").

``ActionClassSerializer``      — read shape (full row).
``ActionClassWriteSerializer`` — create payload (name + type + config + description).
``ActionClassUpdateSerializer``— PATCH payload (all optional; partial update).

``config`` is accepted as an opaque object here; the deep type-vs-config validation is the
service-layer ``validate_action_config`` step (the single write-path validator — mirrors the
Survey serializer/validator split).
"""
from __future__ import annotations

from rest_framework import serializers

from .enums import ActionType
from .models import ActionClass


class ActionClassSerializer(serializers.ModelSerializer):
    """Read shape — the full ActionClass row."""

    environment_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = ActionClass
        fields = [
            "id",
            "environment_id",
            "name",
            "description",
            "type",
            "config",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ActionClassWriteSerializer(serializers.Serializer):
    """Create payload. ``name`` + ``type`` required; ``config``/``description`` optional.

    ``type`` is validated against the ActionType choices here (a fast envelope check); the
    config-vs-type validation runs in the service.
    """

    name = serializers.CharField(max_length=255)
    type = serializers.ChoiceField(choices=ActionType.choices, default=ActionType.CODE)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    config = serializers.DictField(required=False, default=dict)


class ActionClassUpdateSerializer(serializers.Serializer):
    """PATCH payload — every field optional (partial update)."""

    name = serializers.CharField(max_length=255, required=False)
    type = serializers.ChoiceField(choices=ActionType.choices, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    config = serializers.DictField(required=False)
