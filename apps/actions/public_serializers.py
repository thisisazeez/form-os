"""
DRF serializers for the PUBLIC widget APIs (FEATURE_PARITY WS-11).

``WidgetTrackSerializer``    — the ``POST .../actions/track`` request body.
``WidgetAppSurveySerializer``— one app-survey item in the response of BOTH widget endpoints.

The app-survey item is a LIGHTWEIGHT trigger descriptor (id + name + triggers + display_options),
NOT the full survey definition. The widget, once it decides to show a survey, fetches the full
renderable definition from the EXISTING public survey-definition endpoint
(``GET /api/v1/<org>/public/surveys/{id}``, apps.surveys.public_serializers.PublicSurveySerializer)
and runs the EXISTING runtime. So this serializer deliberately omits questions/styling/etc. — it
only carries what the widget needs to DECIDE + fire (id, name, triggers, display_options).
"""
from __future__ import annotations

from rest_framework import serializers

from apps.surveys.models import Survey


class WidgetTrackSerializer(serializers.Serializer):
    """``POST .../environments/<env>/actions/track`` body.

    ``userId`` (client-supplied external id) + ``action`` (the tracked action name/key) are
    required; ``attributes`` (optional contact traits) + ``url`` (optional pageview context)
    are accepted for forward-compat.
    """

    userId = serializers.CharField(max_length=255)
    action = serializers.CharField(max_length=255)
    attributes = serializers.DictField(required=False, default=dict)
    url = serializers.CharField(required=False, allow_blank=True, default="")


class WidgetDisplaySerializer(serializers.Serializer):
    """``POST .../app-surveys/<survey_id>/displays`` body — just the ``userId`` to key the
    Display's contact to (the survey id is in the path)."""

    userId = serializers.CharField(max_length=255)


class WidgetAppSurveySerializer(serializers.ModelSerializer):
    """One eligible app-survey, as returned to the widget. Lightweight: id + name + the trigger
    metadata the widget needs to fire it. The full renderable definition comes from the existing
    public survey-definition endpoint."""

    class Meta:
        model = Survey
        fields = [
            "id",
            "name",
            "survey_type",
            "triggers",
            "display_options",
        ]
        read_only_fields = fields
