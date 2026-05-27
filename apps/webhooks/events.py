"""
Webhook event payload builders — the JSON ``data`` shape sent for each event.

Kept separate from services/tasks so the survey/response call sites build a payload without
importing delivery machinery, and so the payload shape is documented in one place. Payloads are
intentionally compact (ids + a few descriptive fields), never the full survey definition or
respondent PII — receivers fetch detail via the API with their own key if they need more.
"""
from __future__ import annotations

__all__ = ["survey_payload", "response_payload"]


def survey_payload(survey) -> dict:
    """``data`` for survey.published / survey.closed."""
    return {
        "survey_id": str(survey.id),
        "name": survey.name,
        "status": survey.status,
        "environment_id": str(survey.environment_id),
    }


def response_payload(response) -> dict:
    """``data`` for response.created / response.finished."""
    return {
        "response_id": str(response.id),
        "survey_id": str(response.survey_id),
        "finished": response.finished,
        "language": response.language,
    }
