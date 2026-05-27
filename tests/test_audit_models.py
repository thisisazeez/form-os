"""
Focused model tests for audit (AuditLog, contracts/models.md §2.16; Phase 7 model frozen now).

Coverage: append-only shape (created_at only, NO updated_at), metadata JSONB round-trip, the
nullable actor_user_id cross-schema pattern (+ null = system), and ip as a nullable
GenericIPAddressField (PII, config-gated).
"""
import uuid

import pytest
from django.db import connection

from apps.audit.models import AuditLog


@pytest.mark.django_db
def test_auditlog_append_only_no_updated_at(demo_tenant):
    """Append-only: created_at only — NO updated_at (models.md §2.16)."""
    field_names = {f.name for f in AuditLog._meta.get_fields()}
    assert "created_at" in field_names
    assert "updated_at" not in field_names


@pytest.mark.django_db
def test_auditlog_metadata_roundtrip_and_actor_null(demo_tenant):
    connection.set_tenant(demo_tenant)
    meta = {"before": {"status": "draft"}, "after": {"status": "published"}}
    log = AuditLog.objects.create(
        actor_user_id=None,  # null = system/anonymous (models.md §2.16)
        action="survey.published",
        target_type="survey",
        target_id=str(uuid.uuid4()),
        metadata=meta,
        ip="203.0.113.7",
    )
    log.refresh_from_db()
    assert log.metadata == meta
    assert log.actor_user_id is None
    assert log.ip == "203.0.113.7"


@pytest.mark.django_db
def test_auditlog_actor_user_id_no_fk(demo_tenant):
    field = AuditLog._meta.get_field("actor_user_id")
    assert field.get_internal_type() == "UUIDField"
    assert not field.is_relation
    assert field.null is True


@pytest.mark.django_db
def test_auditlog_actor_hydrates(demo_tenant, django_assert_num_queries):
    """actor_user_id → actor_user_obj (the field-minus-_id + suffix rule); the resolution is
    ONE public-schema query (models.md PART A)."""
    from apps.accounts.models import User
    from apps.common.hydration import hydrate_users

    connection.set_schema_to_public()
    actor = User.objects.create_user(email="actor@example.com", password="x", name="Actor")

    connection.set_tenant(demo_tenant)
    AuditLog.objects.create(
        actor_user_id=actor.id, action="member.invited", target_type="membership"
    )
    # Materialize first; the hydration step is the single public-schema query.
    rows = list(AuditLog.objects.all())
    with django_assert_num_queries(1):
        hydrate_users(rows, fields=("actor_user_id",))
    assert rows[0].actor_user_obj == actor

    # Manager ergonomic also works (chained on the default queryset).
    assert AuditLog.objects.all().hydrate_users("actor_user_id")[0].actor_user_obj == actor
