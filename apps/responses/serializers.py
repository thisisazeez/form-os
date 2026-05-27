"""
DRF serializers for the Response READ API (contracts/openapi.yaml — Responses tag, read side).

Wave 2c implements ONLY the authenticated read side (list/detail). The public submission path
(POST/PATCH/finalize via token) is the runtime wave and is NOT served here.

Response shapes mirror the openapi components:
  - ``Response`` — {id, survey_id, data, finished, contact_id, language, meta, started_at,
    finished_at, updated_at, tags[]}.
  - ``Tag``      — {id, name, color}.

PII (rbac.md §11): ``meta.ip`` is PII and is "redacted for non-privileged callers" (openapi
``Response.meta`` description). The read API is gated at workspace ``read`` and does not model a
finer privilege tier, so we strip ``ip`` from the returned ``meta`` by default — the raw IP is
never echoed back through this surface.
"""
from __future__ import annotations

from rest_framework import serializers

from .models import Response, Tag


class TagSerializer(serializers.ModelSerializer):
    """openapi ``Tag`` = {id, name, color}."""

    class Meta:
        model = Tag
        fields = ["id", "name", "color"]
        read_only_fields = fields


class ResponseSerializer(serializers.ModelSerializer):
    """openapi ``Response`` — the full read shape, including attached ``tags``.

    ``tags`` is read from the ``response_tags`` through-rows; the view ``prefetch_related``\\s
    ``response_tags__tag`` so this is N+1-free over a listed page. ``meta`` has ``ip`` redacted
    (rbac.md §11)."""

    survey_id = serializers.UUIDField(read_only=True)
    contact_id = serializers.UUIDField(read_only=True, allow_null=True)
    meta = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()

    class Meta:
        model = Response
        fields = [
            "id",
            "survey_id",
            "data",
            "finished",
            "contact_id",
            "language",
            "meta",
            "started_at",
            "finished_at",
            "updated_at",
            "tags",
        ]
        read_only_fields = fields

    def get_meta(self, obj) -> dict:
        """Return ``meta`` with the PII ``ip`` key removed (rbac.md §11)."""
        meta = dict(obj.meta or {})
        meta.pop("ip", None)
        return meta

    def get_tags(self, obj) -> list:
        """Tags attached to this response, via the prefetched through-rows (no per-row query)."""
        tags = [rt.tag for rt in obj.response_tags.all()]
        return TagSerializer(tags, many=True).data
