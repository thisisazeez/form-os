"""
Focused model tests for responses (Response / SurveyLink / Display / Tag / ResponseTag,
contracts/models.md §2.7–§2.9).

Key coverage: Response.data JSONB round-trip + a GIN-index-backed containment query (PLAN
§7/§10), the contact_id-is-UUID-not-FK modeling choice (ADR-008), the unique (response, tag)
join constraint, and the deliberate timestamp shapes (Display = shown_at only; ResponseTag =
created_at only — models.md §2.8/§2.9).
"""
import uuid

import pytest
from django.db import IntegrityError, connection

from apps.responses.models import Display, Response, ResponseTag, SurveyLink, Tag
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace


def _make_survey(demo_tenant):
    connection.set_tenant(demo_tenant)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    return Survey.objects.create(environment=env, name="S", questions=[])


@pytest.mark.django_db
def test_response_data_jsonb_roundtrip(demo_tenant):
    """data is JSONB keyed by question id → answer; round-trips losslessly (models.md §2.7)."""
    survey = _make_survey(demo_tenant)
    data = {"nps": 9, "why": "Great product", "features": ["speed", "ui"]}
    meta = {"url": "https://x/y", "source": "link", "userAgent": "pytest", "deviceType": "desktop"}
    r = Response.objects.create(survey=survey, data=data, meta=meta, finished=True)
    r.refresh_from_db()
    assert r.data == data
    assert r.meta == meta
    assert r.finished is True


@pytest.mark.django_db
def test_response_gin_index_containment_query(demo_tenant):
    """A JSONB containment lookup (the kind the gin_response_data index accelerates) returns
    the right rows. (Functional check — the index existence is verified at the DB level too.)"""
    survey = _make_survey(demo_tenant)
    Response.objects.create(survey=survey, data={"nps": 9})
    Response.objects.create(survey=survey, data={"nps": 3})
    # `data__contains` compiles to the @> operator backed by the GIN index.
    hits = Response.objects.filter(survey=survey, data__contains={"nps": 9})
    assert hits.count() == 1
    assert hits.first().data["nps"] == 9


@pytest.mark.django_db
def test_response_contact_id_is_uuid_not_fk(demo_tenant):
    """contact_id is a UUIDField, NOT an FK to Contact (ADR-008): anonymous responses + GDPR
    erasure must not cascade-delete responses. Reviewers must not 'fix' this into an FK."""
    field = Response._meta.get_field("contact_id")
    assert field.get_internal_type() == "UUIDField"
    assert not field.is_relation
    assert field.null is True


@pytest.mark.django_db
def test_response_contact_erasure_anonymizes_not_cascades(demo_tenant):
    """Modeling intent (ADR-008): nulling contact_id leaves the response intact (anonymize),
    which is only possible because it's a UUID column, not a cascading FK."""
    survey = _make_survey(demo_tenant)
    cid = uuid.uuid4()
    r = Response.objects.create(survey=survey, data={"a": 1}, contact_id=cid)
    # Simulate erasure: null the contact reference; the response row survives.
    r.contact_id = None
    r.save(update_fields=["contact_id"])
    r.refresh_from_db()
    assert r.contact_id is None
    assert Response.objects.filter(id=r.id).exists()


@pytest.mark.django_db
def test_surveylink_token_unique_and_response_set_null(demo_tenant):
    survey = _make_survey(demo_tenant)
    r = Response.objects.create(survey=survey, data={})
    link = SurveyLink.objects.create(survey=survey, token="tok-abc", response=r)
    with pytest.raises(IntegrityError):
        SurveyLink.objects.create(survey=survey, token="tok-abc")  # token unique
    # Deleting the response SET_NULLs the link's response (models.md §2.7a), link survives.
    connection.set_tenant(demo_tenant)
    r.delete()
    link.refresh_from_db()
    assert link.response_id is None


@pytest.mark.django_db
def test_display_has_only_shown_at(demo_tenant):
    """Display is an append-only impression: only shown_at — NO created_at/updated_at
    (models.md §2.8)."""
    field_names = {f.name for f in Display._meta.get_fields()}
    assert "shown_at" in field_names
    assert "created_at" not in field_names
    assert "updated_at" not in field_names
    survey = _make_survey(demo_tenant)
    d = Display.objects.create(survey=survey)
    assert d.shown_at is not None


@pytest.mark.django_db
def test_tag_unique_and_response_tag_join(demo_tenant):
    survey = _make_survey(demo_tenant)
    r = Response.objects.create(survey=survey, data={})
    tag = Tag.objects.create(name="VIP", color="#ff0000")
    ResponseTag.objects.create(response=r, tag=tag)
    # (response, tag) is unique (models.md §2.9).
    with pytest.raises(IntegrityError):
        ResponseTag.objects.create(response=r, tag=tag)


@pytest.mark.django_db
def test_tag_name_case_insensitive_unique(demo_tenant):
    connection.set_tenant(demo_tenant)
    Tag.objects.create(name="Urgent")
    with pytest.raises(IntegrityError):
        Tag.objects.create(name="urgent")  # uniq_tag_name = Lower("name")


@pytest.mark.django_db
def test_response_tag_has_only_created_at(demo_tenant):
    """ResponseTag (M2M through) carries only created_at — NO updated_at (models.md §2.9)."""
    field_names = {f.name for f in ResponseTag._meta.get_fields()}
    assert "created_at" in field_names
    assert "updated_at" not in field_names
