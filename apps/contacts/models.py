"""
contacts — TENANT schema (TENANT_APPS). The platform's primary PII store (models.md §2.10–§2.12, rbac.md §11).

TENANT-schema models. FK(Environment)/FK(Contact)/FK(ContactAttributeKey) are tenant→tenant
(OK). `created_by_user_id` is the cross-schema User pattern (ADR-002).

Email/phone are NOT first-class columns — they are attributes (ADR-010): a ContactAttribute
keyed by a well-known ContactAttributeKey (e.g. `email`). Keeps the contact schema generic
and segment-friendly; `is_pii` drives export redaction.
"""
from django.db import models

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel
from apps.workspaces.models import Environment

from .enums import AttrType


class Contact(TimeStampedUUIDModel):
    """Survey respondent identity (models.md §2.10). Contacts scope to an Environment."""

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="contacts",
    )
    # Caller-supplied stable id (e.g. CRM id). NOT a User FK — the field name deliberately
    # avoids `user_id` to prevent confusion with the cross-schema-User pattern (models.md §2.10).
    external_id = models.CharField(max_length=255, null=True, blank=True)
    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True, blank=True)

    objects = UserHydrationManager()

    class Meta:
        db_table = "contacts_contact"
        constraints = [
            # Unique external_id per environment (only when external_id is set).
            models.UniqueConstraint(
                fields=["environment", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="uniq_contact_external_per_env",
            ),
        ]
        indexes = [
            models.Index(fields=["environment"], name="ix_contact_environment"),
        ]

    def __str__(self) -> str:
        return f"contact {self.external_id or self.id}"


class ContactAttributeKey(TimeStampedUUIDModel):
    """Typed attribute definition (models.md §2.11). Drives segment-filter operator validity
    and export redaction (is_pii)."""

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="attribute_keys",
    )
    key = models.CharField(max_length=128)  # e.g. email, plan, signup_date
    label = models.CharField(max_length=255, blank=True)
    data_type = models.CharField(
        max_length=16, choices=AttrType.choices, default=AttrType.STRING
    )
    # Flag for export redaction / handling (rbac.md §11).
    is_pii = models.BooleanField(default=False)

    class Meta:
        db_table = "contacts_contactattributekey"
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "key"], name="uniq_attr_key_per_env"
            ),
        ]
        indexes = [
            models.Index(fields=["environment"], name="ix_attrkey_environment"),
        ]

    def __str__(self) -> str:
        return f"{self.key} ({self.data_type})"


class ContactAttribute(TimeStampedUUIDModel):
    """Attribute value (models.md §2.12). Stored as text; cast per attribute_key.data_type
    at query/segment time."""

    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="attributes",
    )
    attribute_key = models.ForeignKey(
        ContactAttributeKey,
        on_delete=models.CASCADE,
        related_name="values",
    )
    value = models.TextField()

    class Meta:
        db_table = "contacts_contactattribute"
        constraints = [
            # One value per (contact, key) (models.md §2.12).
            models.UniqueConstraint(
                fields=["contact", "attribute_key"], name="uniq_contact_attr"
            ),
        ]
        indexes = [
            # Segment filtering.
            models.Index(fields=["attribute_key"], name="ix_contactattr_key"),
            models.Index(fields=["contact"], name="ix_contactattr_contact"),
        ]

    def __str__(self) -> str:
        return f"{self.contact_id}.{self.attribute_key_id} = {self.value[:32]}"
