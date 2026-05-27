"""
Focused model tests for surveys (Survey, contracts/models.md §2.6).

Key coverage: JSONB round-trip for every JSON column — especially welcome_card (null vs set)
and endings (load-bearing for the logic engine's jump-target validation, ADR-004) — plus the
JSONField defaults (logic=[], languages=[], styling={}, endings=[]) and the cross-schema
created_by_user_id pattern. Model only persists JSON; shape validation is Wave 2c (app-side).
"""
import uuid

import pytest
from django.db import connection

from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace


def _make_env(demo_tenant):
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    return Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)


@pytest.mark.django_db
def test_survey_defaults(demo_tenant):
    """JSON columns default per the contract: logic/languages/endings = [], styling = {},
    welcome_card/close_on = null, status = draft."""
    env = _make_env(demo_tenant)
    s = Survey.objects.create(environment=env, name="Q1 NPS", questions=[])
    assert s.status == SurveyStatus.DRAFT
    assert s.logic == []
    assert s.languages == []
    assert s.styling == {}
    assert s.endings == []
    assert s.welcome_card is None
    assert s.close_on is None
    assert s.single_use is False
    assert s.published_at is None


@pytest.mark.django_db
def test_survey_jsonb_roundtrip_all_columns(demo_tenant):
    """Write rich JSON to every JSON column, reload from the DB, and assert exact equality —
    confirms JSONB (de)serialization is lossless for the shapes the logic engine relies on."""
    env = _make_env(demo_tenant)

    questions = [
        {
            "id": "nps",
            "type": "rating",
            "headline": {"default": "How likely are you to recommend us?"},
            "range": 10,
            "logic": [
                {
                    "conditions": {"operator": "lessThan", "value": 7},
                    "action": {"type": "jumpTo", "target": "why_low"},
                }
            ],
        },
        {"id": "why_low", "type": "openText", "headline": {"default": "Why?"}},
    ]
    languages = [{"code": "en", "label": "English", "default": True}]
    styling = {"brandColor": "#1a73e8", "roundness": 8}
    welcome_card = {"enabled": True, "headline": {"default": "Welcome!"}}
    # endings is load-bearing: a logic endSurvey.endingId / jumpTo.target may reference an
    # ending id, so it MUST persist server-side for the jump-target validator (ADR-004).
    endings = [
        {"id": "thanks", "type": "endScreen", "headline": {"default": "Thank you!"}},
        {"id": "early_exit", "type": "endScreen", "headline": {"default": "Bye"}},
    ]
    close_on = {"by_date": "2026-12-31T23:59:59Z", "by_response_cap": 1000}

    s = Survey.objects.create(
        environment=env,
        name="Full",
        status=SurveyStatus.PUBLISHED,
        questions=questions,
        languages=languages,
        styling=styling,
        welcome_card=welcome_card,
        endings=endings,
        close_on=close_on,
        single_use=True,
        created_by_user_id=uuid.uuid4(),
    )

    s.refresh_from_db()
    assert s.questions == questions
    assert s.questions[0]["logic"][0]["action"]["target"] == "why_low"
    assert s.languages == languages
    assert s.styling == styling
    assert s.welcome_card == welcome_card
    # The ending ids that the logic validator must be able to resolve survived the round-trip.
    assert [e["id"] for e in s.endings] == ["thanks", "early_exit"]
    assert s.close_on == close_on
    assert s.single_use is True


@pytest.mark.django_db
def test_survey_created_by_user_id_no_fk(demo_tenant):
    """created_by_user_id is a plain UUID column — NO FK to the public User (ADR-002)."""
    field = Survey._meta.get_field("created_by_user_id")
    assert field.get_internal_type() == "UUIDField"
    assert not field.is_relation


@pytest.mark.django_db
def test_survey_hydrate_users_creator(demo_tenant, django_assert_num_queries):
    """Survey.created_by_user_id resolves to a public User; the resolution is ONE public-schema
    query (the field-minus-_id rule: created_by_user_id → created_by_user_obj)."""
    from apps.accounts.models import User
    from apps.common.hydration import hydrate_users

    connection.set_schema_to_public()
    author = User.objects.create_user(email="author@example.com", password="x", name="Author")

    env = _make_env(demo_tenant)
    Survey.objects.create(
        environment=env, name="Authored", questions=[], created_by_user_id=author.id
    )

    # Materialize first; the hydration step is the single public-schema query (models.md PART A).
    rows = list(Survey.objects.all())
    with django_assert_num_queries(1):
        hydrate_users(rows, fields=("created_by_user_id",))
    assert rows[0].created_by_user_obj == author

    # Manager ergonomic also works (chained on the default queryset).
    assert Survey.objects.all().hydrate_users("created_by_user_id")[0].created_by_user_obj == author
