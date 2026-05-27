"""
Workspace services — workspace creation (+ auto environments) and updates.

Single home for workspace business logic in the TENANT schema. DRF views call these. The
key invariant (models.md §2.1 + openapi POST /workspaces "auto-creates production +
development environments") is enforced HERE so no surface can create a workspace without its
two environments.

Typed errors map to HTTP codes in the view layer:
  - ``SlugTakenError`` / generic ``ValidationError`` → 400/409

NOTE on the stale model docstring: ``apps/workspaces/models.py`` says env auto-creation is
"deferred to the Wave 2c service layer". This IS that service layer (Wave 2b owns the
workspace/teams API per the producer brief); the contract (openapi) requires the behavior at
creation, so it lives here, in one transaction with the workspace insert.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.text import slugify

from .enums import EnvType
from .models import Environment, Workspace


class SlugTakenError(ValidationError):
    """A workspace with this slug already exists in the tenant (→ 409)."""


def _derive_unique_slug(name: str, explicit_slug: str | None) -> str:
    """Return a tenant-unique slug: use ``explicit_slug`` if given, else slugify ``name``.

    Uniqueness is scoped to the tenant schema (each tenant has its own ``workspaces_workspace``
    table — the ``uniq_workspace_slug`` constraint is automatically per-tenant). If the chosen
    slug collides, append ``-2``, ``-3``, … (only when the slug was AUTO-derived; an explicit
    slug that collides is a 409 so the caller knows their choice was taken).
    """
    explicit = (explicit_slug or "").strip()
    if explicit:
        base = explicit
        allow_suffix = False
    else:
        base = slugify(name) or "workspace"
        allow_suffix = True

    base = base[:255]
    if not Workspace.objects.filter(slug=base).exists():
        return base
    if not allow_suffix:
        raise SlugTakenError({"slug": f"A workspace with slug '{base}' already exists."})

    # Auto-derived collision → suffix until free.
    n = 2
    while True:
        candidate = f"{base[:250]}-{n}"
        if not Workspace.objects.filter(slug=candidate).exists():
            return candidate
        n += 1


def create_workspace(*, name: str, slug: str | None, created_by_user_id) -> Workspace:
    """Create a ``Workspace`` and its two ``Environment`` rows (production + development).

    All in ONE transaction (models.md §2.1: "On workspace creation: auto-create two
    Environment rows"). A duplicate explicit slug → :class:`SlugTakenError` (409).

    Must be called with the tenant schema ACTIVE (the routing middleware activates it; tests
    use ``connection.set_tenant``).
    """
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Name is required."})

    try:
        with transaction.atomic():
            workspace_slug = _derive_unique_slug(name, slug)
            workspace = Workspace.objects.create(
                name=name, slug=workspace_slug, created_by_user_id=created_by_user_id
            )
            # Auto-create production + development environments (models.md §2.2).
            Environment.objects.create(workspace=workspace, type=EnvType.PRODUCTION)
            Environment.objects.create(workspace=workspace, type=EnvType.DEVELOPMENT)
    except IntegrityError as exc:
        # Race past the in-app uniqueness check → DB constraint wins.
        raise SlugTakenError(
            {"slug": "A workspace with this slug already exists."}
        ) from exc

    return workspace


def update_workspace(*, workspace: Workspace, name: str) -> Workspace:
    """Rename a workspace (slug is immutable). Returns the updated instance."""
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Name is required."})
    workspace.name = name
    workspace.save(update_fields=["name", "updated_at"])
    return workspace


def update_workspace_general(
    *, workspace: Workspace, recontact_days, remove_branding=None
) -> Workspace:
    """Update the workspace's General config: the recontact cooldown + (white-label) branding.

    ≈ Formbricks "Configuration → General" (recontact/cooldown) + the enterprise "remove branding"
    toggle. Both fields are optional to set independently:

      - ``recontact_days``: ``None`` clears the cooldown (no recontact gate); an int >= 0 sets it.
        A negative or non-integer value raises ``ValidationError`` (→ 400 in the view). A blank
        string is treated as ``None`` (the form's "off" state).
      - ``remove_branding``: when not ``None``, sets the boolean. When ``None`` (omitted), the
        existing value is left untouched (so a caller can update one field without the other).

    Kept SEPARATE from ``update_workspace`` (which is a tight name-only rename used by the DRF
    PATCH) so the General-settings surface can evolve without widening that contract.
    """
    update_fields = ["updated_at"]

    # --- recontact_days: normalize "", None → None; coerce/validate ints; reject negatives. ---
    if isinstance(recontact_days, str):
        recontact_days = recontact_days.strip()
        recontact_days = recontact_days if recontact_days != "" else None
    if recontact_days is None:
        workspace.recontact_days = None
    else:
        try:
            value = int(recontact_days)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"recontact_days": "Recontact days must be a whole number."}
            ) from exc
        if value < 0:
            raise ValidationError(
                {"recontact_days": "Recontact days cannot be negative."}
            )
        workspace.recontact_days = value
    update_fields.append("recontact_days")

    # --- remove_branding: only touch it when explicitly provided. ---
    if remove_branding is not None:
        workspace.remove_branding = bool(remove_branding)
        update_fields.append("remove_branding")

    workspace.save(update_fields=update_fields)
    return workspace


def update_workspace_theme(*, workspace: Workspace, theme: dict) -> Workspace:
    """Set the workspace's project-level "Look & Feel" theme (``survey_theme``).

    The ``theme`` is the SAME Styling shape a survey uses (#/$defs/styling); it is validated
    against that subschema HERE (one source of truth) before persisting — an invalid shape
    (unknown key, out-of-range roundness, bad enum) raises ``ValidationError`` which the view
    maps to a 400. ``{}`` clears the project theme (surveys then fall back to runtime defaults).
    """
    # Local import: theme.py imports nothing heavy, but keeping it lazy avoids any import-time
    # coupling and mirrors how the surveys app keeps its validator usage at the call site.
    from .theme import validate_theme

    theme = theme or {}
    validate_theme(theme)  # raises ValidationError on a bad shape (→ 400 in the view)
    workspace.survey_theme = theme
    workspace.save(update_fields=["survey_theme", "updated_at"])
    return workspace
