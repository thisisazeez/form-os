"""
surveys enumerations (contracts/models.md PART C).

These string values are the contract — they MUST equal the enums in models.md PART C,
openapi.yaml, and survey-schema.json. Any change is a coordinated re-broadcast (PLAN §15).

  SurveyStatus = draft | published | paused | closed
  SurveyType   = link | app
"""
from django.db import models


class SurveyStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    PAUSED = "paused", "Paused"
    CLOSED = "closed", "Closed"


class SurveyType(models.TextChoices):
    """The survey's delivery surface (FEATURE_PARITY WS-11; mirrors Formbricks's survey type).

    - ``link`` (default) — a shareable URL survey (our v1 surface). EVERY existing survey is a
      link survey and behaves byte-identically.
    - ``app``            — a website/app (in-product) survey shown by the embeddable JS widget,
      TRIGGERED by ActionClass events (``Survey.triggers``) and gated by ``display_options`` +
      the workspace recontact cooldown. Served via the public widget APIs (apps.actions.public_*),
      NOT the link runtime.
    """

    LINK = "link", "Link"
    APP = "app", "App"
