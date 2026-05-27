"""
DRF serializers for the Survey API (contracts/openapi.yaml — Surveys tag).

Response shapes mirror the openapi components:
  - ``Survey``        — full definition (questions/logic/languages/styling/welcome_card/
    endings/close_on are passed through opaquely; their shapes are validated on write
    against survey-schema.json, NOT re-described here).
  - ``SurveySummary`` — lightweight listing item (omits the heavy questions/logic blobs;
    carries ``response_count``).

Write payloads (``SurveyWrite``) are validated by ``apps/surveys/validators.py`` in the
view/service, NOT here — DRF field validation only covers the thin envelope (name,
environment_id, single_use); the deep questions/logic/languages/styling/etc. validation is
the jsonschema + in-code ``validate_survey`` step (the single source of truth, ADR-004).
"""
from __future__ import annotations

from rest_framework import serializers

from .models import Survey


class SurveySerializer(serializers.ModelSerializer):
    """openapi ``Survey`` — the full definition. JSON columns are returned verbatim."""

    environment_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Survey
        fields = [
            "id",
            "environment_id",
            "name",
            "status",
            "single_use",
            "capture_ip",
            "questions",
            "logic",
            "languages",
            "styling",
            "welcome_card",
            "endings",
            "close_on",
            "hidden_fields",
            "follow_ups",
            "blocks",
            "variables",
            "quotas",
            "survey_type",
            "triggers",
            "display_options",
            "published_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SurveySummarySerializer(serializers.ModelSerializer):
    """openapi ``SurveySummary`` — listing item; omits questions/logic; adds response_count.

    ``response_count`` is read from a ``response_count`` annotation the view attaches once
    (``Count("responses")``) to avoid an N+1 over the listed surveys.
    """

    environment_id = serializers.UUIDField(read_only=True)
    response_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Survey
        fields = [
            "id",
            "name",
            "status",
            "environment_id",
            "response_count",
            "published_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SurveyWriteSerializer(serializers.Serializer):
    """openapi ``SurveyWrite`` — create/replace payload (envelope only).

    Required: ``name``, ``questions``, ``languages`` (per the openapi/schema top-level
    required keys). ``environment_id`` is REQUIRED in the openapi body, but on the nested
    ``POST /environments/{environment_id}/surveys`` route the path is the source of truth;
    the view passes the path env and rejects a conflicting body value (400). For PUT it is
    immutable and ignored. The deep questions/logic/languages/styling/welcome_card/endings/
    close_on shapes are validated against survey-schema.json + in-code checks downstream.
    """

    name = serializers.CharField(max_length=255)
    environment_id = serializers.UUIDField(required=False)
    single_use = serializers.BooleanField(required=False, default=False)
    capture_ip = serializers.BooleanField(required=False, default=False)
    # The JSON blobs: accepted as opaque structures here; validated downstream.
    questions = serializers.ListField(child=serializers.DictField(), allow_empty=True)
    logic = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    languages = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    styling = serializers.DictField(required=False, default=dict)
    welcome_card = serializers.DictField(required=False, allow_null=True, default=None)
    endings = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    close_on = serializers.DictField(required=False, allow_null=True, default=None)
    # Survey-level hidden fields [{id, name?}]; opaque here, validated downstream against
    # #/$defs/hiddenFields (the hidden_fields -> hiddenFields camelCase translation).
    hidden_fields = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    # Survey follow-ups [{id, name?, trigger, action}]; opaque here, validated downstream
    # against #/$defs/followUps (the follow_ups -> followUps camelCase translation) + the
    # in-code follow-up cross-reference checks.
    follow_ups = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    # Question-grouping blocks [{id, title?, questionIds:[...], randomize?}]; opaque here,
    # validated downstream against #/$defs/block (the `blocks` key is the SAME in snake/camel,
    # so no rename) + the in-code block cross-reference checks (questionIds resolve; no
    # question in two blocks; unique block ids).
    blocks = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    # Author-defined survey variables [{id, name, type, initialValue}]; opaque here, validated
    # downstream against #/$defs/variable (the `variables` key is the SAME in snake/camel, so no
    # rename) + the in-code variable cross-reference checks (unique ids across the shared
    # namespace; calculate.variableId resolves; type-compatible operation; ref values resolve).
    variables = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    # Response quotas [{id, name, limit, condition, endingId?}]; opaque here, validated downstream
    # against #/$defs/quota (the `quotas` key is the SAME in snake/camel, so no rename) + the
    # in-code quota cross-reference checks (unique quota ids; endingId resolves to a declared
    # ending; condition refs resolve with a type-valid operator).
    quotas = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    # --- Website/App (in-product) survey fields (FEATURE_PARITY WS-11) --------------------- #
    # All three are OPTIONAL with NO default (unlike the JSON blobs above): an OMITTED field
    # arrives as absent (not None/[]), so the view passes None to create_survey/replace_survey,
    # which means "default to link/[]/{} on create" and "leave as-is on PUT". This preserves
    # byte-identical behavior for every caller that doesn't know about these fields (link surveys).
    # survey_type ∈ {link, app}; choice-validated here (fast envelope check) + downstream.
    survey_type = serializers.ChoiceField(choices=["link", "app"], required=False)
    # triggers: ActionClass id STRINGS (kept as strings — stored verbatim in the JSON column;
    # shape (UUID-ness) + DB-resolution validated downstream, not coerced to UUID objects here).
    triggers = serializers.ListField(child=serializers.CharField(), required=False)
    # display_options: {displayLimit?, displayPercentage?} (opaque here; validated downstream).
    display_options = serializers.DictField(required=False)


class SurveyStatusUpdateSerializer(serializers.Serializer):
    """openapi ``POST /surveys/{id}/status`` body — ``{status}``.

    The transition's validity and the per-target RBAC action (publish/pause/close) are
    enforced in the service/view (openapi ``x-permission-note``)."""

    status = serializers.ChoiceField(
        choices=["draft", "published", "paused", "closed"]
    )
