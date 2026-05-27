"""
DRF serializers for Contacts + ContactAttributeKeys (contracts/openapi.yaml — Contacts tag).

Response shapes mirror the openapi components:
  - ``Contact`` = {id, environment_id, external_id, attributes[], created_at}
       attributes[] = {key, value, data_type, is_pii}
  - ``ContactWrite`` = {external_id?, attributes[]: {key, value}}
  - ``ContactAttributeKey`` (no explicit openapi schema — added with the attribute-key
    endpoints; see views/urls): {id, environment_id, key, label, data_type, is_pii, created_at}

PII REDACTION (openapi: a value is "Redacted/masked if the attribute key is_pii and caller
lacks privilege"). The contract leaves "privilege" undefined; we DEFINE it as:
  - A caller with workspace access >= ``write`` (which includes org owner/admin via implicit
    manage) sees RAW PII values.
  - A ``read``-only caller sees PII values MASKED as ``"***"`` (the key/data_type/is_pii flag
    are still returned so the UI can render the field, just not the value).
The view injects ``pii_unmasked`` (bool) into the serializer context; the serializer never
computes RBAC itself (single source of truth = can()/effective_access in the view).
"""
from __future__ import annotations

from rest_framework import serializers

from .enums import AttrType
from .models import Contact, ContactAttributeKey

PII_MASK = "***"


class ContactAttributeReadField(serializers.Serializer):
    """One attribute in the ``Contact.attributes`` array (read shape)."""

    key = serializers.CharField()
    value = serializers.CharField()
    data_type = serializers.CharField()
    is_pii = serializers.BooleanField()


class ContactSerializer(serializers.ModelSerializer):
    """openapi ``Contact``. ``attributes`` is built from the prefetched related rows; PII
    values are masked unless the view set ``context['pii_unmasked']`` True."""

    environment_id = serializers.UUIDField(read_only=True)
    attributes = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = ["id", "environment_id", "external_id", "attributes", "created_at"]
        read_only_fields = fields

    def get_attributes(self, obj) -> list[dict]:
        unmasked = self.context.get("pii_unmasked", False)
        out: list[dict] = []
        # Uses prefetched obj.attributes (attributes__attribute_key) — no per-row query.
        for attr in obj.attributes.all():
            key = attr.attribute_key
            value = attr.value
            if key.is_pii and not unmasked:
                value = PII_MASK
            out.append(
                {
                    "key": key.key,
                    "value": value,
                    "data_type": key.data_type,
                    "is_pii": key.is_pii,
                }
            )
        # Stable ordering for deterministic responses/tests.
        out.sort(key=lambda a: a["key"])
        return out


class _AttributeWriteItem(serializers.Serializer):
    """One {key, value} in a ContactWrite ``attributes`` array. Both required (openapi)."""

    key = serializers.CharField(max_length=128)
    value = serializers.CharField(allow_blank=True, trim_whitespace=False)


class ContactWriteSerializer(serializers.Serializer):
    """openapi ``ContactWrite`` — {external_id?, attributes[]}. Used for POST and PATCH.

    Typed-VALUE validation (against each key's ``data_type``) happens in the service layer
    (``contacts.services.validate_attribute_value``), not here, because it needs the
    environment's keys; this serializer enforces only the STRUCTURE.
    """

    external_id = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )
    attributes = _AttributeWriteItem(many=True, required=False)

    def validate_attributes(self, value):
        # Reject duplicate keys in one payload (ambiguous; one value per key — models.md §2.12).
        keys = [a["key"] for a in value]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("Duplicate attribute keys in payload.")
        return value


class ContactAttributeKeySerializer(serializers.ModelSerializer):
    """Read shape for a ContactAttributeKey (attribute-key management endpoints)."""

    environment_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = ContactAttributeKey
        fields = ["id", "environment_id", "key", "label", "data_type", "is_pii", "created_at"]
        read_only_fields = fields


class ContactAttributeKeyCreateSerializer(serializers.Serializer):
    """POST attribute-key — {key, data_type?, label?, is_pii?}. Defaults data_type=string."""

    key = serializers.CharField(max_length=128)
    data_type = serializers.ChoiceField(
        choices=AttrType.choices, required=False, default=AttrType.STRING
    )
    label = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    is_pii = serializers.BooleanField(required=False, default=False)


class ContactAttributeKeyUpdateSerializer(serializers.Serializer):
    """PATCH attribute-key — any of {label, is_pii, data_type}."""

    label = serializers.CharField(max_length=255, required=False, allow_blank=True)
    is_pii = serializers.BooleanField(required=False)
    data_type = serializers.ChoiceField(choices=AttrType.choices, required=False)
