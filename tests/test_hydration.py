"""
Tests for the frozen cross-schema helper `hydrate_users()` (ADR-002 / models.md PART A).

This is load-bearing Wave-1 infrastructure: every tenant app that references a User uses
it. The hard guarantees: ONE public-schema query regardless of input size (no N+1), and
`*_obj` attributes attached per the naming rule (user_id -> user_obj, etc.).
"""
import uuid

import pytest
from django.db import connection

from apps.common.hydration import hydrate_users


@pytest.mark.django_db
def test_hydrate_users_attaches_objects_one_query(demo_tenant, django_assert_num_queries):
    """From INSIDE a tenant schema, hydrate_users resolves public Users in ONE query."""
    from apps.accounts.models import User

    # Create users in the public schema.
    connection.set_schema_to_public()
    u1 = User.objects.create_user(email="a@example.com", password="x", name="Alice")
    u2 = User.objects.create_user(email="b@example.com", password="x", name="Bob")

    # Simulate tenant rows carrying user_id columns (no FK — the cross-schema pattern).
    connection.set_tenant(demo_tenant)

    class FakeRow:
        def __init__(self, user_id):
            self.user_id = user_id

    rows = [FakeRow(u1.id), FakeRow(u2.id), FakeRow(None)]

    # Exactly one query against the public schema (the N+1 this helper exists to kill).
    with django_assert_num_queries(1):
        result = hydrate_users(rows, fields=("user_id",))

    assert result[0].user_obj == u1
    assert result[1].user_obj == u2
    assert result[2].user_obj is None  # None user_id resolves to None


@pytest.mark.django_db
def test_hydrate_users_custom_field_name(demo_tenant):
    """`actor_user_id` -> `actor_user_obj` (the field-minus-_id + suffix rule)."""
    from apps.accounts.models import User

    connection.set_schema_to_public()
    actor = User.objects.create_user(email="c@example.com", password="x")

    connection.set_tenant(demo_tenant)

    class AuditRow:
        def __init__(self, actor_user_id):
            self.actor_user_id = actor_user_id

    rows = [AuditRow(actor.id)]
    hydrate_users(rows, fields=("actor_user_id",))
    assert rows[0].actor_user_obj == actor


@pytest.mark.django_db
def test_hydrate_users_empty_input_no_query(django_assert_num_queries):
    """No referenced ids → no public-schema query at all."""
    with django_assert_num_queries(0):
        assert hydrate_users([], fields=("user_id",)) == []


@pytest.mark.django_db
def test_hydrate_users_unknown_id_resolves_none(demo_tenant):
    """A user_id with no matching User resolves to None (app-level integrity, ADR-002)."""
    connection.set_tenant(demo_tenant)

    class FakeRow:
        def __init__(self, user_id):
            self.user_id = user_id

    rows = [FakeRow(uuid.uuid4())]
    hydrate_users(rows, fields=("user_id",))
    assert rows[0].user_obj is None
