"""
ActionClass services — config validation + create / update / delete. Single home for
ActionClass business logic in the TENANT schema (DRF views + the public widget call these).

VALIDATION (mirrors the Survey JSON-column convention: the model persists opaque JSON; the
service is the single write-path validator):
  - ``type`` ∈ ActionType (code|pageview|click).
  - ``config`` keys validated against the type:
      code     → optional ``key`` (string; defaults to ``name`` for SDK ``track``).
      pageview → optional ``urlMatch`` (string).
      click    → optional ``cssSelector`` (string).
    Unknown config keys are REJECTED (additionalProperties:false semantics) so a typo'd
    ``urlmatch`` surfaces as a 400 rather than silently never matching.

Name uniqueness within the environment is enforced by the model's
``uniq_actionclass_name_per_env`` constraint; the view maps the IntegrityError to a 409.
"""
from __future__ import annotations

from django.db import transaction

from .enums import ActionType
from .models import ActionClass

__all__ = [
    "ActionConfigError",
    "validate_action_config",
    "create_action_class",
    "update_action_class",
    "delete_action_class",
]

# Allowed config keys per action type (additionalProperties:false semantics).
_CONFIG_KEYS: dict[str, set[str]] = {
    ActionType.CODE: {"key"},
    ActionType.PAGEVIEW: {"urlMatch"},
    ActionType.CLICK: {"cssSelector"},
}


class ActionConfigError(ValueError):
    """An ActionClass ``type``/``config`` is invalid (→ 400). ``errors`` is a list of
    ``{"field": ..., "message": ...}`` for the API to surface."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("Invalid action config.")


def validate_action_config(*, action_type: str, config: dict | None) -> dict:
    """Validate ``config`` for ``action_type``; return the canonical config dict.

    Raises ``ActionConfigError`` (aggregating every problem) for a bad type, a non-dict
    config, an unknown config key, or a non-string config value. An empty/omitted config is
    valid for every type (the widget falls back to ``name`` for code; pageview/click without
    a match simply never auto-fire server-side — the widget owns that matching).
    """
    errors: list[dict] = []

    if action_type not in ActionType.values:
        errors.append(
            {"field": "type", "message": f"type must be one of {ActionType.values}"}
        )
        # Without a valid type we cannot validate config keys; surface the type error alone.
        raise ActionConfigError(errors)

    config = config or {}
    if not isinstance(config, dict):
        raise ActionConfigError([{"field": "config", "message": "config must be an object"}])

    allowed = _CONFIG_KEYS[action_type]
    for key, value in config.items():
        if key not in allowed:
            errors.append(
                {
                    "field": f"config.{key}",
                    "message": f"unknown key for type '{action_type}' (allowed: {sorted(allowed)})",
                }
            )
            continue
        if value is not None and not isinstance(value, str):
            errors.append(
                {"field": f"config.{key}", "message": f"'{key}' must be a string"}
            )

    if errors:
        raise ActionConfigError(errors)
    # Drop null values so the stored config is clean (a null match is "no match").
    return {k: v for k, v in config.items() if v is not None}


def create_action_class(
    *,
    environment,
    name: str,
    action_type: str = ActionType.CODE,
    description: str = "",
    config: dict | None = None,
    created_by_user_id=None,
) -> ActionClass:
    """Validate + create an ActionClass in ``environment``.

    Name uniqueness within the env is enforced by the DB constraint (the view maps the
    resulting IntegrityError to 409). Must be called with the tenant schema ACTIVE.
    """
    clean_config = validate_action_config(action_type=action_type, config=config)
    with transaction.atomic():
        return ActionClass.objects.create(
            environment=environment,
            name=name,
            description=description or "",
            type=action_type,
            config=clean_config,
            created_by_user_id=created_by_user_id,
        )


def update_action_class(
    *,
    action_class: ActionClass,
    name: str | None = None,
    action_type: str | None = None,
    description: str | None = None,
    config: object = "__unset__",
) -> ActionClass:
    """PATCH-style update. Re-validates config against the (possibly new) type.

    ``config="__unset__"`` sentinel = "not supplied". Supplying only ``config`` (without
    ``type``) re-validates it against the EXISTING type. Supplying only ``type`` re-validates
    the EXISTING config against the new type (so a code→pageview switch with a stale ``key``
    config is rejected rather than silently kept).
    """
    fields: list[str] = []
    if name is not None:
        action_class.name = name
        fields.append("name")
    if description is not None:
        action_class.description = description
        fields.append("description")

    new_type = action_type if action_type is not None else action_class.type
    new_config = action_class.config if config == "__unset__" else config
    if action_type is not None or config != "__unset__":
        clean_config = validate_action_config(action_type=new_type, config=new_config)
        action_class.type = new_type
        action_class.config = clean_config
        if action_type is not None:
            fields.append("type")
        fields.append("config")

    if fields:
        fields.append("updated_at")
        with transaction.atomic():
            action_class.save(update_fields=fields)
    return action_class


def delete_action_class(*, action_class: ActionClass) -> None:
    """Delete an ActionClass.

    NOTE: ``Survey.triggers`` references ActionClass ids by VALUE (a JSON list, not an FK),
    so deleting an action class does NOT cascade into surveys; a survey trigger referencing a
    deleted action simply never matches (the public matcher resolves live ActionClass rows).
    This is the intended decoupling (mirrors Survey logic referencing ending ids by value)."""
    action_class.delete()
