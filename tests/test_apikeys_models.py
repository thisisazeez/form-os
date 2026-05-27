"""
Focused model tests for apikeys (ApiKey, contracts/models.md §2.15).

Coverage: hashed_key uniqueness (only the hash is stored, never the raw key — REVIEW.md §3.6),
scopes JSONField default, and the cross-schema created_by_user_id pattern.
"""
import uuid

import pytest
from django.db import IntegrityError, connection

from apps.apikeys.models import ApiKey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace


def _make_env(demo_tenant):
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    return Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)


@pytest.mark.django_db
def test_apikey_hashed_key_unique_and_scopes_default(demo_tenant):
    env = _make_env(demo_tenant)
    k = ApiKey.objects.create(
        environment=env, label="CI key", hashed_key="sha256:abc", prefix="osk_live_"
    )
    assert k.scopes == []  # default=list
    assert k.revoked_at is None and k.last_used_at is None
    # hashed_key is globally unique (auth lookup) — duplicate hash rejected.
    with pytest.raises(IntegrityError):
        ApiKey.objects.create(
            environment=env, label="dup", hashed_key="sha256:abc", prefix="osk_live_"
        )


@pytest.mark.django_db
def test_apikey_stores_only_hash_not_raw(demo_tenant):
    """There is no raw-key column — only hashed_key + a non-secret prefix (models.md §2.15)."""
    field_names = {f.name for f in ApiKey._meta.get_fields()}
    assert "hashed_key" in field_names
    assert "prefix" in field_names
    assert "key" not in field_names
    assert "raw_key" not in field_names


@pytest.mark.django_db
def test_apikey_created_by_user_id_no_fk(demo_tenant):
    field = ApiKey._meta.get_field("created_by_user_id")
    assert field.get_internal_type() == "UUIDField"
    assert not field.is_relation
