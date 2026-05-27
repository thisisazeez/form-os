"""
DRF serializers for the PUBLIC response write path (openapi "Public Survey" tag).

Shapes mirror the openapi components:
  - ``PublicResponseCreate``  — create payload {data?, language?, link_token?, meta?}. A
    client-supplied ``contact_id`` is intentionally NOT a field here: contact association is
    server-derived from ``link_token`` ONLY (ADR-009 / rbac.md §11). Even if a caller adds
    ``contact_id`` to the JSON, it is dropped (unknown field → ignored by this serializer).
  - ``PublicResponseHandle`` — {id, finished, edit_token, expires_at}. The runtime carries
    ``edit_token`` (ADR-009) to PATCH/finalize this one response.
  - ``PublicResponsePatch``   — {data, language?} merged into Response.data.

These are thin envelopes — the deep checks (answer keys vs the survey's question set;
required-answer gate at finalize) live in ``apps/responses/public_services.py``, the single
business-logic home, so no surface can bypass them.
"""
from __future__ import annotations

from rest_framework import serializers


class PublicResponseCreateSerializer(serializers.Serializer):
    """openapi ``PublicResponseCreate`` (the public create body).

    NOTE the deliberate omission of ``contact_id``: contact is derived from ``link_token``
    server-side (the reconciled D-fix). ``data`` may be empty (a response can be created
    before the first answer)."""

    data = serializers.DictField(required=False, default=dict)
    language = serializers.CharField(required=False, default="default", max_length=10)
    link_token = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None, max_length=64
    )
    meta = serializers.DictField(required=False, default=dict)


class PublicResponsePatchSerializer(serializers.Serializer):
    """openapi PATCH body — ``{data, language?}``. ``data`` is a partial answer map."""

    data = serializers.DictField(required=True)
    language = serializers.CharField(required=False, max_length=10)


class PublicResponseHandleSerializer(serializers.Serializer):
    """openapi ``PublicResponseHandle`` — what the runtime gets back to continue editing.

    Built from a plain dict (not a model) so the view can attach the freshly-minted
    ``edit_token`` + its ``expires_at`` (neither is a model column — ADR-009).

    QUOTAS (additive): the over-quota fields (``over_quota`` + ``quota_full_ending_id``) are NOT
    declared here — DRF can't conditionally OMIT a declared field cleanly (an ``allow_null`` field
    still emits ``null``), and we want a non-over-quota handle to be BYTE-IDENTICAL to the pre-quota
    one. The finalize view MERGES those keys onto this serializer's output ONLY for an over-quota
    response (``finalize_handle_body``)."""

    id = serializers.UUIDField()
    finished = serializers.BooleanField()
    edit_token = serializers.CharField()
    expires_at = serializers.DateTimeField()
