"""
Focused model tests for contacts (Contact / ContactAttributeKey / ContactAttribute,
contracts/models.md §2.10–§2.12).

Key coverage: email/phone are attributes not columns (ADR-010), the conditional unique
external_id-per-environment constraint (only when external_id is set), one-value-per-key,
and the data_type enum.
"""
import uuid

import pytest
from django.db import IntegrityError, connection

from apps.contacts.enums import AttrType
from apps.contacts.models import Contact, ContactAttribute, ContactAttributeKey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace


def _make_env(demo_tenant):
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    return Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)


@pytest.mark.django_db
def test_contact_has_no_email_or_phone_columns(demo_tenant):
    """Email/phone are attributes, not columns (ADR-010). Only external_id exists as a
    caller-supplied id; there must be no email/phone/user_id field on Contact."""
    field_names = {f.name for f in Contact._meta.get_fields()}
    assert "external_id" in field_names
    assert "email" not in field_names
    assert "phone" not in field_names
    assert "user_id" not in field_names  # deliberately avoided (models.md §2.10)


@pytest.mark.django_db
def test_contact_external_id_unique_per_env_only_when_set(demo_tenant):
    env = _make_env(demo_tenant)
    Contact.objects.create(environment=env, external_id="crm-1")
    # Duplicate non-null external_id in the same env violates the partial unique constraint.
    with pytest.raises(IntegrityError):
        Contact.objects.create(environment=env, external_id="crm-1")


@pytest.mark.django_db
def test_contact_multiple_null_external_ids_allowed(demo_tenant):
    """The unique constraint is conditioned on external_id IS NOT NULL — many anonymous
    contacts (null external_id) coexist in one environment (models.md §2.10)."""
    env = _make_env(demo_tenant)
    Contact.objects.create(environment=env)
    Contact.objects.create(environment=env)  # no IntegrityError
    assert Contact.objects.filter(environment=env, external_id__isnull=True).count() == 2


@pytest.mark.django_db
def test_attribute_key_unique_per_env_and_data_type(demo_tenant):
    env = _make_env(demo_tenant)
    ContactAttributeKey.objects.create(
        environment=env, key="email", data_type=AttrType.STRING, is_pii=True
    )
    with pytest.raises(IntegrityError):
        ContactAttributeKey.objects.create(environment=env, key="email")  # uniq_attr_key_per_env


@pytest.mark.django_db
def test_contact_attribute_one_value_per_key(demo_tenant):
    env = _make_env(demo_tenant)
    contact = Contact.objects.create(environment=env, external_id="c-1")
    key = ContactAttributeKey.objects.create(environment=env, key="plan")
    ContactAttribute.objects.create(contact=contact, attribute_key=key, value="pro")
    # One value per (contact, key) (models.md §2.12).
    with pytest.raises(IntegrityError):
        ContactAttribute.objects.create(contact=contact, attribute_key=key, value="free")


@pytest.mark.django_db
def test_contact_attribute_roundtrip_via_relations(demo_tenant):
    env = _make_env(demo_tenant)
    contact = Contact.objects.create(environment=env, external_id="c-2")
    email_key = ContactAttributeKey.objects.create(
        environment=env, key="email", data_type=AttrType.STRING, is_pii=True
    )
    ContactAttribute.objects.create(
        contact=contact, attribute_key=email_key, value="user@example.com"
    )
    # Read the email back through the generic attribute model (ADR-010 — no email column).
    val = contact.attributes.get(attribute_key__key="email").value
    assert val == "user@example.com"
    assert contact.attributes.get(attribute_key__key="email").attribute_key.is_pii is True
