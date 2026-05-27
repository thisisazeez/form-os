"""
Tests for the global top-bar search (apps.common.views.search_page):

  GET /{org_slug}/search?q=  → surveys (by name, current-workspace scoped) + contacts
                               (by attribute value, readable-env scoped, PII-masked).

Hits the real TenantSlugMiddleware path with Django's session test Client + force_login.
Org creation provisions a Postgres schema (DDL) → transactional_db + force_drop cleanup.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client

from apps.contacts.models import Contact, ContactAttribute, ContactAttributeKey
from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "search-org"


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def ctx(transactional_db):
    """Org + owner + one controlled workspace/env + a PUBLISHED survey + a contact with an email."""
    connection.set_schema_to_public()
    User.objects.filter(email="search_owner@example.com").delete()
    for org in Organization.objects.filter(slug=SLUG):
        org.delete(force_drop=True)
    owner = User.objects.create_user(
        email="search_owner@example.com", password="x", is_verified=True
    )
    org = create_organization(name="Search Org", slug=SLUG, owner=owner)

    connection.set_tenant(org)
    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="Main WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = Survey.objects.create(
        environment=env,
        name="Customer Satisfaction Survey",
        status=SurveyStatus.PUBLISHED,
        questions=[{"id": "q1", "type": "openText", "headline": {"default": "Hi?"}, "required": False, "config": {}}],
        languages=[{"code": "en", "label": "English", "default": True}],
        styling={},
        endings=[],
    )
    email_key = ContactAttributeKey.objects.create(
        environment=env, key="email", data_type="string", is_pii=True, label="Email"
    )
    contact = Contact.objects.create(environment=env)
    ContactAttribute.objects.create(
        contact=contact, attribute_key=email_key, value="findme@example.com"
    )
    connection.set_schema_to_public()
    yield {"org": org, "owner": owner, "env": env, "survey": survey, "contact": contact}
    _cleanup()


def _cleanup():
    connection.set_schema_to_public()
    User.objects.filter(email="search_owner@example.com").delete()
    for org in Organization.objects.filter(slug=SLUG):
        org.delete(force_drop=True)


def test_search_matches_survey_by_name(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/search?q=Customer")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Customer Satisfaction Survey" in body
    assert f"/{SLUG}/surveys/{ctx['survey'].id}/edit" in body


def test_search_matches_contact_by_attribute(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/search?q=findme")
    assert resp.status_code == 200
    body = resp.content.decode()
    # The contact is linkable (owner has unmasked PII access → value shown too).
    assert f"/{SLUG}/contacts/{ctx['contact'].id}" in body
    assert "findme@example.com" in body


def test_search_no_query_renders_empty_state(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/search")
    assert resp.status_code == 200
    assert "Search your workspace" in resp.content.decode()


def test_search_requires_login(client, ctx):
    resp = client.get(f"/{SLUG}/search?q=Customer")
    assert resp.status_code in (302, 301)
