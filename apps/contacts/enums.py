"""
contacts enumerations (contracts/models.md PART C).

These string values are the contract — they MUST equal the enums in models.md PART C,
openapi.yaml, and survey-schema.json. Any change is a coordinated re-broadcast (PLAN §15).

  AttrType = string | number | boolean | date
"""
from django.db import models


class AttrType(models.TextChoices):
    STRING = "string", "String"
    NUMBER = "number", "Number"
    BOOLEAN = "boolean", "Boolean"
    DATE = "date", "Date"
