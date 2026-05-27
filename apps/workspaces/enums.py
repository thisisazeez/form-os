"""
workspaces enumerations (contracts/models.md PART C).

These string values are the contract — they MUST equal the enums in models.md PART C,
openapi.yaml, and survey-schema.json. Any change is a coordinated re-broadcast (PLAN §15).

  EnvType = production | development
"""
from django.db import models


class EnvType(models.TextChoices):
    PRODUCTION = "production", "Production"
    DEVELOPMENT = "development", "Development"
