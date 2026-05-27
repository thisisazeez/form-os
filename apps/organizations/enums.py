"""
Public-schema enumerations (contracts/models.md PART C).

These string values are the contract — they MUST equal the enums in models.md PART C,
openapi.yaml, and survey-schema.json. Any change is a coordinated re-broadcast (PLAN §15).

  OrgRole          = owner | admin | member          (ADR-007: no `billing` in v1)
  MembershipStatus = active | suspended
  InviteStatus     = pending | accepted | revoked | expired
"""
from django.db import models


class OrgRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class MembershipStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class InviteStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REVOKED = "revoked", "Revoked"
    EXPIRED = "expired", "Expired"
