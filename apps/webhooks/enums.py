"""
webhooks enumerations (contracts/models.md PART C).

These string values are the contract — they MUST equal the enums in models.md PART C,
openapi.yaml, and survey-schema.json. Any change is a coordinated re-broadcast (PLAN §15).

  WebhookEvent = response.created | response.finished | survey.published | survey.closed

Webhook.events is a JSONField holding a subset of these strings (models.md §2.14); this
enum is the canonical value set for app-level validation (Phase 7), not a DB choices set.
"""
from django.db import models


class WebhookEvent(models.TextChoices):
    RESPONSE_CREATED = "response.created", "Response created"
    RESPONSE_FINISHED = "response.finished", "Response finished"
    SURVEY_PUBLISHED = "survey.published", "Survey published"
    SURVEY_CLOSED = "survey.closed", "Survey closed"
