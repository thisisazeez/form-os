"""
Focused model tests for workspaces (Workspace + Environment, contracts/models.md §2.1–§2.2).

Runs INSIDE a tenant schema (the `demo_tenant` fixture from tests/conftest.py provisions it).
Covers: creation, UUID PK shape, the schema-scoped slug uniqueness, and the one-prod-one-dev
per-workspace constraint.
"""
import uuid

import pytest
from django.db import IntegrityError, connection

from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace


@pytest.mark.django_db
def test_workspace_creation_uuid_pk_and_timestamps(demo_tenant):
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="Marketing", slug="marketing")
    assert isinstance(ws.id, uuid.UUID)  # ADR-003 UUID PK
    assert ws.created_at is not None and ws.updated_at is not None
    # created_by_user_id is the cross-schema User pattern — a plain nullable UUID, no FK.
    assert ws.created_by_user_id is None


@pytest.mark.django_db
def test_workspace_slug_unique_within_tenant(demo_tenant):
    connection.set_tenant(demo_tenant)
    Workspace.objects.create(name="A", slug="dupe")
    with pytest.raises(IntegrityError):
        Workspace.objects.create(name="B", slug="dupe")


@pytest.mark.django_db
def test_environment_one_per_type_per_workspace(demo_tenant):
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="WS", slug="ws")
    Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    Environment.objects.create(workspace=ws, type=EnvType.DEVELOPMENT)
    # A second production env for the same workspace violates uniq_env_per_workspace_type.
    with pytest.raises(IntegrityError):
        Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)


@pytest.mark.django_db
def test_environment_fk_cascade_is_same_schema(demo_tenant):
    """Environment→Workspace is a tenant→tenant FK (same schema) — deleting the workspace
    cascades to its environments (models.md §2.2)."""
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="WS2", slug="ws2")
    Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    ws.delete()
    assert Environment.objects.filter(workspace_id=ws.id).count() == 0
