"""
Contacts services — typed-attribute validation, dedup-by-email upsert, attribute-key
management, and GDPR-safe deletion. The single home for contacts business logic in the
TENANT schema (DRF views call these).

DESIGN DECISIONS (the brief grants latitude here; documented so reviewers see the choice):

DEDUP-BY-EMAIL (models.md leaves it unspecified → we default to UPSERT-BY-EMAIL within the
environment, the Formbricks-shaped choice):
  - The dedup key is the ``email`` attribute (well-known ``ContactAttributeKey.key == "email"``),
    compared CASE-INSENSITIVELY on its VALUE within the target Environment.
  - On create (POST /environments/{id}/contacts) with an ``email`` attribute that already
    matches an existing contact → UPSERT: merge the supplied attributes into the existing
    contact (new keys added, supplied keys overwrite, untouched keys preserved) and return it
    with HTTP 200. A genuinely new email (or no email) → create a new contact, HTTP 201.
    SUPPLIED ATTRIBUTES ALWAYS WIN — including ``email`` itself: the dedup MATCH is
    case-insensitive on the email value, but the stored value is then overwritten with the
    newly-supplied casing (email is not special-cased among the merged keys; the caller sent
    it explicitly, so a later ``Bob@Ex.com`` replaces an earlier ``bob@ex.com``).
  - If no ``email`` attribute is supplied, fall back to ``external_id`` matching (the model's
    partial-unique constraint already enforces external_id uniqueness per env); a matching
    external_id upserts, else create.
  - (Alternative considered: always-201, never merge. Rejected for v1 — re-identifying a
    returning respondent by email is the common case and silent duplicate contacts fragment
    their response history. The choice is localized to ``upsert_contact`` and easy to flip.)

ATTRIBUTE TYPING:
  - ``ContactAttributeKey.data_type`` ∈ {string, number, boolean, date} (models.md §2.11).
  - On contact write, an UNKNOWN attribute key is AUTO-CREATED as ``string`` (lowest-friction,
    Formbricks-shaped). Callers manage types explicitly via the attribute-key endpoints.
  - Every supplied value is VALIDATED against its key's declared type before being stored
    (``validate_attribute_value``): number → Decimal (reject NaN/Inf), boolean →
    true/false/1/0, date → ISO-8601 (``date.fromisoformat``), string → as-is. A bad value
    raises ``AttributeValueError`` (→ 400 with per-key errors).

GDPR ERASURE (openapi DELETE /contacts/{id} "Responses are anonymized, not deleted"):
  - ``delete_contact`` NULLs the contact's id on every tenant row that references it by id
    (``Response.contact_id``, ``Display.contact_id``, ``SurveyLink.contact_id`` — all plain
    UUIDFields, NOT FKs, ADR-008) IN THE SAME TRANSACTION, then hard-deletes the Contact (its
    ``ContactAttribute`` rows cascade). Historical responses survive, de-identified.
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models.functions import Lower

from .enums import AttrType
from .models import Contact, ContactAttribute, ContactAttributeKey

EMAIL_KEY = "email"


class AttributeValueError(ValueError):
    """A supplied attribute value is invalid for its key's data_type.

    ``errors`` is a list of ``{"key": ..., "value": ..., "message": ...}`` for the API to
    surface under ``errors.attributes`` (openapi ValidationErrorBody).
    """

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("Invalid attribute value(s).")


# ---------------------------------------------------------------------------
# Typed-value validation (text storage; cast-checked at write time).
# ---------------------------------------------------------------------------
def validate_attribute_value(value: str, data_type: str) -> str:
    """Validate ``value`` (always a string in the write payload) for ``data_type``; return the
    canonical text to store. Raises ``ValueError`` (message only) on a bad value — callers wrap
    into ``AttributeValueError`` with the key context.

    - number : parseable as Decimal; NaN/Inf rejected. Stored as the original text.
    - boolean: one of true/false/1/0 (case-insensitive). Stored normalized to "true"/"false".
    - date   : ISO-8601 date (``YYYY-MM-DD``). Stored as the ISO text.
    - string : any text, stored as-is.
    """
    if not isinstance(value, str):
        # The serializer declares value as a string; guard defensively.
        raise ValueError("value must be a string")
    if data_type == AttrType.NUMBER:
        try:
            dec = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"'{value}' is not a valid number") from exc
        if dec.is_nan() or dec.is_infinite() or math.isinf(float(dec)):
            raise ValueError(f"'{value}' is not a finite number")
        return value
    if data_type == AttrType.BOOLEAN:
        lowered = value.strip().lower()
        if lowered in ("true", "1"):
            return "true"
        if lowered in ("false", "0"):
            return "false"
        raise ValueError(f"'{value}' is not a valid boolean (use true/false)")
    if data_type == AttrType.DATE:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"'{value}' is not a valid ISO-8601 date (YYYY-MM-DD)") from exc
        return value
    # string (or any unknown type) → as-is.
    return value


# ---------------------------------------------------------------------------
# Attribute-key management.
# ---------------------------------------------------------------------------
def get_or_create_attribute_key(
    *, environment, key: str, data_type: str = AttrType.STRING, label: str = "", is_pii: bool = False
) -> ContactAttributeKey:
    """Fetch or create a ``ContactAttributeKey`` (unique per (environment, key)).

    Used by the contact-write path to AUTO-CREATE unknown keys as ``string`` (the documented
    low-friction default). Does NOT change an existing key's ``data_type`` (types are managed
    explicitly via the attribute-key endpoints).
    """
    obj, _created = ContactAttributeKey.objects.get_or_create(
        environment=environment,
        key=key,
        defaults={"data_type": data_type, "label": label, "is_pii": is_pii},
    )
    return obj


def create_attribute_key(
    *, environment, key: str, data_type: str, label: str = "", is_pii: bool = False
) -> ContactAttributeKey:
    """Create a typed attribute key. Raises ``ValueError`` on a bad data_type; the unique
    (environment, key) constraint surfaces as IntegrityError (→ 409 in the view)."""
    key = (key or "").strip()
    if not key:
        raise ValueError("key is required")
    if data_type not in AttrType.values:
        raise ValueError(f"data_type must be one of {AttrType.values}")
    return ContactAttributeKey.objects.create(
        environment=environment, key=key, data_type=data_type, label=label, is_pii=is_pii
    )


def update_attribute_key(
    *, attribute_key: ContactAttributeKey, label=None, is_pii=None, data_type=None
) -> ContactAttributeKey:
    """Patch a key's ``label`` / ``is_pii`` / ``data_type``. Changing ``data_type`` does NOT
    re-validate stored values (legacy values that don't fit simply stop matching comparisons —
    see filters._coerce); callers should change types deliberately."""
    fields: list[str] = []
    if label is not None:
        attribute_key.label = label
        fields.append("label")
    if is_pii is not None:
        attribute_key.is_pii = is_pii
        fields.append("is_pii")
    if data_type is not None:
        if data_type not in AttrType.values:
            raise ValueError(f"data_type must be one of {AttrType.values}")
        attribute_key.data_type = data_type
        fields.append("data_type")
    if fields:
        fields.append("updated_at")
        attribute_key.save(update_fields=fields)
    return attribute_key


# ---------------------------------------------------------------------------
# Contact create / upsert (dedup-by-email).
# ---------------------------------------------------------------------------
def _find_email_value(attributes: list[dict]) -> str | None:
    """Return the supplied ``email`` attribute value for dedup, or None.

    A BLANK/whitespace email is treated as "no email" (None) so it does NOT become a dedup key
    (otherwise every contact with an empty email would collapse into one). The blank value is
    still stored as a normal attribute by the merge step; it just doesn't drive dedup.
    """
    for attr in attributes:
        if attr.get("key") == EMAIL_KEY:
            value = attr.get("value")
            if isinstance(value, str) and value.strip():
                return value
            return None
    return None


def _find_contact_by_email(environment, email_value: str) -> Contact | None:
    """Case-insensitive lookup of a contact by its ``email`` attribute value in the env."""
    if email_value is None:
        return None
    match = (
        ContactAttribute.objects.annotate(value_ci=Lower("value"))
        .filter(
            contact__environment=environment,
            attribute_key__key=EMAIL_KEY,
            value_ci=email_value.strip().lower(),
        )
        .select_related("contact")
        .first()
    )
    return match.contact if match else None


def _validate_all_values(environment, attributes: list[dict]) -> list[tuple[ContactAttributeKey, str]]:
    """Resolve/auto-create each key and validate each value. Returns ``[(key_obj, stored_text)]``.

    Raises ``AttributeValueError`` aggregating every bad value (does not stop at the first).
    Unknown keys are auto-created as ``string``. Auto-creation happens here (inside the caller's
    transaction) so a later validation failure rolls the new keys back too.
    """
    resolved: list[tuple[ContactAttributeKey, str]] = []
    errors: list[dict] = []
    # Cache keys we resolve so two attrs with the same key in one payload don't double-create.
    key_cache: dict[str, ContactAttributeKey] = {}
    for attr in attributes:
        key = attr.get("key")
        value = attr.get("value")
        if key not in key_cache:
            key_cache[key] = get_or_create_attribute_key(environment=environment, key=key)
        key_obj = key_cache[key]
        try:
            stored = validate_attribute_value(value, key_obj.data_type)
        except ValueError as exc:
            errors.append({"key": key, "value": value, "message": str(exc)})
            continue
        resolved.append((key_obj, stored))
    if errors:
        raise AttributeValueError(errors)
    return resolved


def _apply_attributes(contact: Contact, resolved: list[tuple[ContactAttributeKey, str]]) -> None:
    """Upsert each (key, value) onto the contact (one ContactAttribute per key — uniq constraint)."""
    for key_obj, stored in resolved:
        ContactAttribute.objects.update_or_create(
            contact=contact, attribute_key=key_obj, defaults={"value": stored}
        )


def upsert_contact(
    *, environment, external_id=None, attributes: list[dict] | None = None, created_by_user_id=None
) -> tuple[Contact, bool]:
    """Create a contact, OR merge attributes into an existing one (dedup-by-email; see module
    docstring). Returns ``(contact, created)`` — ``created`` is True on insert, False on upsert.

    Dedup precedence: ``email`` attribute (case-insensitive value) → ``external_id`` → new row.
    All work runs in ONE transaction so a value-validation failure leaves no partial state.
    Must be called with the tenant schema ACTIVE.
    """
    attributes = attributes or []
    email_value = _find_email_value(attributes)

    with transaction.atomic():
        # Validate (and auto-create keys for) all values up front.
        resolved = _validate_all_values(environment, attributes)

        # 1. Dedup by email value (case-insensitive) within the environment.
        existing = _find_contact_by_email(environment, email_value)
        # 2. Fall back to external_id match.
        if existing is None and external_id:
            existing = Contact.objects.filter(
                environment=environment, external_id=external_id
            ).first()

        if existing is not None:
            # Upsert: merge attributes; optionally backfill external_id if newly supplied.
            if external_id and not existing.external_id:
                existing.external_id = external_id
                existing.save(update_fields=["external_id", "updated_at"])
            _apply_attributes(existing, resolved)
            return existing, False

        # 3. Create a new contact.
        contact = Contact.objects.create(
            environment=environment,
            external_id=external_id or None,
            created_by_user_id=created_by_user_id,
        )
        _apply_attributes(contact, resolved)
        return contact, True


def update_contact_attributes(
    *, contact: Contact, external_id="__unset__", attributes: list[dict] | None = None
) -> Contact:
    """PATCH a contact's attributes (and optionally ``external_id``). Merges (does not replace
    the full attribute set): supplied keys are upserted, omitted keys are left untouched.

    ``external_id="__unset__"`` sentinel = "not supplied, leave as-is"; an explicit value
    (including ``None``) updates it. All in one transaction.
    """
    attributes = attributes or []
    with transaction.atomic():
        resolved = _validate_all_values(contact.environment, attributes)
        if external_id != "__unset__":
            contact.external_id = external_id or None
            contact.save(update_fields=["external_id", "updated_at"])
        _apply_attributes(contact, resolved)
    return contact


# ---------------------------------------------------------------------------
# GDPR-safe deletion (anonymize references; hard-delete the contact).
# ---------------------------------------------------------------------------
def delete_contact(*, contact: Contact) -> None:
    """Delete a contact, ANONYMIZING (not cascade-deleting) its responses (openapi DELETE
    semantics + rbac.md §11 GDPR). NULLs ``contact_id`` on ``Response`` / ``Display`` /
    ``SurveyLink`` (plain UUIDFields, ADR-008) in the SAME transaction, then hard-deletes the
    Contact (its ``ContactAttribute`` rows cascade via FK).

    Must be called with the tenant schema ACTIVE.
    """
    # Local import — responses is outside this producer's territory; we only READ its model
    # classes to null a UUID column (no schema/contract change).
    from apps.responses.models import Display, Response, SurveyLink

    contact_id = contact.id
    with transaction.atomic():
        Response.objects.filter(contact_id=contact_id).update(contact_id=None)
        Display.objects.filter(contact_id=contact_id).update(contact_id=None)
        SurveyLink.objects.filter(contact_id=contact_id).update(contact_id=None)
        contact.delete()
