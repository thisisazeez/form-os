"""
Focused model tests for segments (Segment, contracts/models.md §2.13).

Coverage: filter JSONB round-trip + default, case-insensitive title uniqueness per env, and
the cross-schema created_by_user_id pattern.
"""
import uuid

import pytest
from django.db import IntegrityError, connection

from apps.segments.models import Segment
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace


def _make_env(demo_tenant):
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    return Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)


@pytest.mark.django_db
def test_segment_filter_default_and_roundtrip(demo_tenant):
    env = _make_env(demo_tenant)
    s = Segment.objects.create(environment=env, title="Power users")
    assert s.filter == {}  # default=dict
    # A boolean tree of attribute conditions (shape per survey-schema.json#/$defs/segmentFilter).
    tree = {
        "connector": "and",
        "conditions": [
            {"attribute": "plan", "operator": "equals", "value": "pro"},
            {"attribute": "signup_date", "operator": "greaterThan", "value": "2026-01-01"},
        ],
    }
    s.filter = tree
    s.save()
    s.refresh_from_db()
    assert s.filter == tree


@pytest.mark.django_db
def test_segment_title_case_insensitive_unique_per_env(demo_tenant):
    env = _make_env(demo_tenant)
    Segment.objects.create(environment=env, title="VIPs")
    with pytest.raises(IntegrityError):
        Segment.objects.create(environment=env, title="vips")  # uniq_segment_title_per_env


@pytest.mark.django_db
def test_segment_created_by_user_id_no_fk(demo_tenant):
    field = Segment._meta.get_field("created_by_user_id")
    assert field.get_internal_type() == "UUIDField"
    assert not field.is_relation
