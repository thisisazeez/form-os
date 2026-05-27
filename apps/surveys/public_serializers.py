"""
DRF serializer for the PUBLIC survey-definition GET (openapi ``PublicSurvey``).

This is the ONLY public-read shape of a Survey. It exposes exactly the fields the runtime
needs to render — questions / languages / styling / welcomeCard / endings / single_use —
and NOTHING else (no environment_id, status, created_by, timestamps, close_on, logic
column). No internal/admin leakage (openapi: "No internal/admin fields").

KEY/SHAPE NOTE (load-bearing — mirrors validators.py's inverse map): the openapi
``PublicSurvey`` keys the welcome card as **camelCase ``welcomeCard``**, but the Django
model column is snake_case ``welcome_card``. We rename on output via a ``source=``. (The
authenticated ``Survey`` schema uses ``welcome_card``; only the PUBLIC shape is camelCase —
this matches the openapi exactly.) ``single_use`` stays snake_case in both.
"""
from __future__ import annotations

from rest_framework import serializers

from .models import Survey


class PublicSurveySerializer(serializers.ModelSerializer):
    """openapi ``PublicSurvey`` — the published definition the runtime renders.

    Fields are a strict allow-list; JSON columns are returned verbatim. ``welcomeCard`` is
    the camelCase output name for the ``welcome_card`` column (openapi PublicSurvey)."""

    welcomeCard = serializers.JSONField(source="welcome_card", allow_null=True)
    # Survey-level hidden fields [{id, name?}] — camelCase on the wire (PublicSurvey),
    # sourced from the snake_case `hidden_fields` column (mirrors the welcomeCard rename).
    # The runtime reads these to capture matching ?param= values into the response.
    hiddenFields = serializers.JSONField(source="hidden_fields")
    # Author-defined survey variables [{id, name, type, initialValue}] — exposed to the runtime
    # so it can SEED + thread variable state (compute_variables) for recall + conditions. Same
    # key on the wire as the column (`variables`), so no source= rename. Default [] == no
    # variables == behavior unchanged.
    # Question-grouping blocks [{id, title?, questionIds, randomize?}] — exposed to the runtime
    # so it can apply WITHIN-BLOCK presentation randomization (seeded per-response). Same key on
    # the wire as the column (`blocks`), so no source= rename (unlike welcomeCard/hiddenFields).
    # Default [] == no blocks == flat behavior unchanged.
    # PROJECT-THEME INHERITANCE (load-bearing): the styling the runtime renders is the survey's
    # own styling composed OVER the workspace's project theme — survey keys win, the project theme
    # fills the rest (apps.workspaces.theme.merge_styling). Default (no project theme + no survey
    # styling) == {} == current behavior unchanged. The workspace chain is already select_related
    # by public_services.get_published_survey, so this adds no query.
    styling = serializers.SerializerMethodField()

    def get_styling(self, survey) -> dict:
        from apps.workspaces.theme import merge_styling

        project_theme = getattr(survey.environment.workspace, "survey_theme", None)
        return merge_styling(project_theme, survey.styling)

    class Meta:
        model = Survey
        fields = [
            "id",
            "name",
            "questions",
            "languages",
            "styling",
            "welcomeCard",
            "endings",
            "hiddenFields",
            "blocks",
            "variables",
            "single_use",
        ]
        read_only_fields = fields
