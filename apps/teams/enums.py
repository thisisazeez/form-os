"""
teams enumerations (contracts/models.md PART C).

These string values are the contract — they MUST equal the enums in models.md PART C,
openapi.yaml, and rbac.md. Any change is a coordinated re-broadcast (PLAN §15).

  TeamRole        = admin | member
  WorkspaceAccess = read | write | manage          # ordered: read < write < manage
"""
from django.db import models


class TeamRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class WorkspaceAccess(models.TextChoices):
    # Ordered read < write < manage. The effective-permission rule (rbac.md §4) takes the
    # MAX access over a user's WorkspaceTeam rows; the ordering is enforced in code
    # (apps/common/permissions.py), not the DB — the enum only fixes the string values.
    READ = "read", "Read"
    WRITE = "write", "Write"
    MANAGE = "manage", "Manage"
