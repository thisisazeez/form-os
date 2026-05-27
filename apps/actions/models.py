"""
actions — TENANT schema (TENANT_APPS). ``ActionClass`` — user-action definitions that
TRIGGER website/app surveys (FEATURE_PARITY WS-11; mirrors Formbricks's ActionClass).

TENANT-schema model. FK(Environment) is tenant→tenant (OK). ``created_by_user_id`` is the
cross-schema User pattern (plain UUIDField, NO FK — ADR-002), resolved via hydrate_users().

An ActionClass is scoped to one Environment and uniquely NAMED within it (the name doubles
as the SDK ``track(name)`` key when ``config.key`` is absent). App-type surveys reference
ActionClass ids in ``Survey.triggers``; the public widget ``track`` endpoint resolves a
tracked action (by ``config.key`` then ``name``) to an ActionClass and returns the surveys
whose triggers include it (apps.actions.public_services).
"""
from django.db import models

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel
from apps.workspaces.models import Environment

from .enums import ActionType


class ActionClass(TimeStampedUUIDModel):
    """A named, per-Environment user-action that can trigger app surveys (FEATURE_PARITY WS-11).

    ``config`` is an opaque JSON blob whose meaningful keys depend on ``type``:
      - ``code``     : ``{"key": "<sdk track key>"}`` (optional; defaults to ``name``).
      - ``pageview`` : ``{"urlMatch": "<glob/substring>"}`` (widget-side URL match).
      - ``click``    : ``{"cssSelector": "<selector>"}`` (widget-side click match).
    The shape is validated app-side (apps.actions.services.validate_action_config); the model
    only persists the JSON (mirrors the Survey JSON-column convention)."""

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="action_classes",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    type = models.CharField(max_length=16, choices=ActionType.choices, default=ActionType.CODE)
    # Type-specific config (validated app-side). Callable default (mutable) per Django rules.
    config = models.JSONField(default=dict, blank=True)
    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True, blank=True)

    objects = UserHydrationManager()

    class Meta:
        db_table = "actions_actionclass"
        constraints = [
            # Action names are unique within an environment (the name doubles as the SDK key).
            models.UniqueConstraint(
                fields=["environment", "name"], name="uniq_actionclass_name_per_env"
            ),
        ]
        indexes = [
            models.Index(fields=["environment"], name="ix_actionclass_environment"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.type})"
