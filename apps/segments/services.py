"""
Segments services — CRUD + filter validation (structural via jsonschema against the FROZEN
``survey-schema.json#/$defs/segmentFilter``, then semantic via the type-aware operator table in
``apps.segments.filters``).

The single home for segment business logic in the TENANT schema (DRF views call these).

FILTER VALIDATION (two layers, both run on every write — openapi: "filter validated against
survey-schema.json#/$defs/segmentFilter", and ``ValidationErrorBody.schema_errors`` carries the
messages):
  1. STRUCTURAL — jsonschema validate the filter against the contract's ``segmentFilter`` def
     (a recursive oneOf of leaf/branch). Catches wrong shapes/types/extra keys.
  2. SEMANTIC — ``filters.validate_filter`` checks each leaf's operator is valid for its
     referenced ``ContactAttributeKey.data_type`` and that value presence/shape fits the
     operator. Catches "greaterThan on a string key", "value on isSkipped", unknown keys.
Both layers' messages are aggregated into ``SegmentFilterError.schema_errors``.
"""
from __future__ import annotations

import functools
import json

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from apps.contacts.models import ContactAttributeKey

from . import filters
from .models import Segment


class SegmentFilterError(ValueError):
    """A segment ``filter`` failed structural and/or semantic validation.

    ``schema_errors`` is the list of messages for openapi ``ValidationErrorBody.schema_errors``.
    """

    def __init__(self, schema_errors: list[str]):
        self.schema_errors = schema_errors
        super().__init__("; ".join(schema_errors) or "Invalid segment filter.")


class SegmentTitleTakenError(ValueError):
    """A segment with this (case-insensitive) title already exists in the environment (→ 409)."""


@functools.lru_cache(maxsize=1)
def _segment_filter_schema() -> dict:
    """Load + cache a self-contained jsonschema for the ``segmentFilter`` def.

    Reads ``contracts/survey-schema.json`` (the FROZEN single source of truth, ADR-004),
    extracts ``$defs/segmentFilter``, and re-roots it with the full ``$defs`` so the recursive
    ``$ref: #/$defs/segmentFilter`` and ``$ref: #/$defs/operator`` resolve under this root.
    Cached because the file never changes at runtime.
    """
    schema_path = settings.BASE_DIR / "contracts" / "survey-schema.json"
    with open(schema_path) as fh:
        full = json.load(fh)
    defs = full.get("$defs", full.get("definitions", {}))
    if "segmentFilter" not in defs:  # guard: the frozen contract must define it (ADR-004).
        raise KeyError("survey-schema.json is missing $defs/segmentFilter")
    # Re-root: the subschema IS segmentFilter, but it references sibling $defs by
    # #/$defs/... — so carry the whole $defs map under the new root.
    return {
        "$schema": full.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "$defs": defs,
        "allOf": [{"$ref": "#/$defs/segmentFilter"}],
    }


def _structural_errors(filter_def) -> list[str]:
    """jsonschema-validate ``filter_def`` against the contract's segmentFilter def. Empty filter
    (``{}``, the match-all default) is allowed and skips structural validation (it is not a
    leaf/branch per the oneOf, but is a meaningful "all contacts" sentinel)."""
    if isinstance(filter_def, dict) and not filter_def:
        return []
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a base requirement.
        return []
    validator = jsonschema.Draft202012Validator(_segment_filter_schema())
    messages: list[str] = []
    try:
        for err in sorted(validator.iter_errors(filter_def), key=lambda e: list(e.path)):
            loc = "filter" + "".join(f"[{p!r}]" for p in err.path)
            messages.append(f"{loc}: {err.message}")
    except RecursionError:
        # Backstop: a pathologically deep tree could overflow jsonschema's own recursion before
        # reaching the depth pre-check. Convert to a structural 400 message (never a 500). The
        # iterative depth pre-check in validate_segment_filter normally catches this first.
        return [
            f"filter: nesting exceeds the maximum depth of {filters.MAX_FILTER_DEPTH}."
        ]
    return messages


def _attribute_types(environment) -> dict[str, str]:
    """``{key: data_type}`` for the environment's ContactAttributeKeys (drives semantic checks)."""
    return dict(
        ContactAttributeKey.objects.filter(environment=environment).values_list(
            "key", "data_type"
        )
    )


def validate_segment_filter(filter_def, *, environment) -> None:
    """Validate a filter both structurally (jsonschema) and semantically (operator/type table).

    Raises ``SegmentFilterError`` aggregating ALL messages from both layers. Run on every
    create/replace before persisting (the model never DB-enforces JSON shape — models.md §2.13).

    SECURITY: a cheap, NON-recursive depth/size pre-check runs FIRST so a pathologically deep or
    wide filter is rejected with a 400 before the recursive jsonschema / semantic passes (which
    would otherwise hit RecursionError → 500). One-request recursion-DoS guard.
    """
    try:
        filters.check_filter_bounds(filter_def)
    except filters.FilterValidationError as exc:
        # Fail fast — don't run the recursive validators on an out-of-bounds tree.
        raise SegmentFilterError(exc.messages) from None

    errors = list(_structural_errors(filter_def))
    # Only run the semantic layer if the structure is sane enough to walk (else messages overlap
    # confusingly). The semantic validator is itself defensive, but skipping on a structural
    # failure keeps the error list focused.
    if not errors:
        try:
            filters.validate_filter(filter_def, attribute_types=_attribute_types(environment))
        except filters.FilterValidationError as exc:
            errors.extend(exc.messages)
    if errors:
        raise SegmentFilterError(errors)


# ---------------------------------------------------------------------------
# CRUD.
# ---------------------------------------------------------------------------
def _title_exists(environment, title: str, *, exclude_id=None) -> bool:
    qs = Segment.objects.annotate(title_ci=Lower("title")).filter(
        environment=environment, title_ci=title.strip().lower()
    )
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    return qs.exists()


def create_segment(*, environment, title: str, filter_def, created_by_user_id=None) -> Segment:
    """Create a segment after validating its filter. Title is unique (case-insensitive) per env.

    Raises ``SegmentFilterError`` (→ 400) on a bad filter, ``SegmentTitleTakenError`` (→ 409) on
    a duplicate title. Must be called with the tenant schema ACTIVE.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    validate_segment_filter(filter_def, environment=environment)
    if _title_exists(environment, title):
        raise SegmentTitleTakenError(f"A segment titled '{title}' already exists.")
    try:
        with transaction.atomic():
            return Segment.objects.create(
                environment=environment,
                title=title,
                filter=filter_def,
                created_by_user_id=created_by_user_id,
            )
    except IntegrityError as exc:  # race past the in-app check → DB constraint wins.
        raise SegmentTitleTakenError(
            f"A segment titled '{title}' already exists."
        ) from exc


def replace_segment(*, segment: Segment, title: str, filter_def) -> Segment:
    """Replace a segment's title + filter (openapi PUT — full replace). Validates the filter
    against the segment's environment. Raises the same typed errors as ``create_segment``."""
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    validate_segment_filter(filter_def, environment=segment.environment)
    if _title_exists(segment.environment, title, exclude_id=segment.id):
        raise SegmentTitleTakenError(f"A segment titled '{title}' already exists.")
    try:
        with transaction.atomic():
            segment.title = title
            segment.filter = filter_def
            segment.save(update_fields=["title", "filter", "updated_at"])
    except IntegrityError as exc:
        raise SegmentTitleTakenError(
            f"A segment titled '{title}' already exists."
        ) from exc
    return segment


def evaluate(segment: Segment, *, limit=None) -> list:
    """Resolve which contacts match a saved segment (delegates to filters.evaluate_segment).
    Must be called with the tenant schema ACTIVE."""
    return filters.evaluate_segment(segment.filter, segment.environment, limit=limit)


def preview(filter_def, *, environment, limit=None) -> list:
    """Resolve matches for an UNSAVED filter (builder count/preview). Validates first so a
    malformed filter returns a 400 rather than silently matching nothing."""
    validate_segment_filter(filter_def, environment=environment)
    return filters.evaluate_segment(filter_def, environment, limit=limit)
