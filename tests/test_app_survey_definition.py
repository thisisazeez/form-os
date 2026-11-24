"""
Tests for the Survey definition carry-through of the website/app fields (FEATURE_PARITY WS-11):
``survey_type`` / ``triggers`` / ``display_options`` through the authenticated Survey CRUD API +
the create/replace service + validation.

DISTINCT DB: DATABASE_URL=postgres://onform:onform@localhost:5432/parity_appsurvey

Covers:
  - default survey_type == "link"; triggers == []; display_options == {} (backward-compatible).
  - creating an app survey with valid triggers (resolving to real ActionClasses) + display_options.
  - rejecting an app survey whose triggers don't resolve to an ActionClass in the env (400).
  - rejecting bad survey_type / display_options shapes (400, via the shape validator).
  - PUT leaves the app fields as-is when omitted (additive PUT semantics).
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.test import APIClient

from apps.actions.models import ActionClass
from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.surveys.enums import SurveyType
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()
SLUG = "appdef-org"


@pytest.fixture
def api():
    return APIClient()


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    connection.set_schema_to_public()
    User.objects.filter(email="appdef_owner@example.com").delete()
    owner = User.objects.create_user(
        email="appdef_owner@example.com", password="x", is_verified=True
    )
    _cleanup(SLUG)
    org = create_organization(name="AppDef Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
    _cleanup(SLUG)


def _body(env_id, **overrides):
    body = {
        "name": "S",
        "questions": [
            {
                "id": "q1",
                "type": "openText",
                "headline": {"default": "x"},
                "required": False,
                "config": {"inputType": "short"},
            }
        ],
        "languages": [{"code": "default", "label": "Default", "default": True}],
    }
    body.update(overrides)
    return body


def _create_url(env_id):
    return f"/api/v1/{SLUG}/environments/{env_id}/surveys"


def _detail_url(survey_id):
    return f"/api/v1/{SLUG}/surveys/{survey_id}"


def test_default_link_survey_unchanged(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(owner)
    resp = api.post(_create_url(env.id), _body(env.id), format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    # Backward-compatible defaults: link / [] / {}.
    assert body["survey_type"] == "link"
    assert body["triggers"] == []
    assert body["display_options"] == {}


def test_create_app_survey_with_triggers(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    action = ActionClass.objects.create(environment=env, name="act", type="code")
    api.force_authenticate(owner)
    resp = api.post(
        _create_url(env.id),
        _body(
            env.id,
            survey_type="app",
            triggers=[str(action.id)],
            display_options={"displayLimit": 3, "displayPercentage": 50},
        ),
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["survey_type"] == "app"
    assert body["triggers"] == [str(action.id)]
    assert body["display_options"] == {"displayLimit": 3, "displayPercentage": 50}
    connection.set_tenant(org)
    survey = Survey.objects.get(pk=body["id"])
    assert survey.survey_type == SurveyType.APP


def test_app_survey_rejects_unresolved_trigger(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(owner)
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = api.post(
        _create_url(env.id),
        _body(env.id, survey_type="app", triggers=[bogus]),
        format="json",
    )
    assert resp.status_code == 400, resp.content
    # The DB-resolution check surfaces under errors.logic.
    assert "errors" in resp.json()


def test_rejects_bad_display_options(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(owner)
    # displayPercentage out of range.
    resp = api.post(
        _create_url(env.id),
        _body(env.id, survey_type="app", display_options={"displayPercentage": 250}),
        format="json",
    )
    assert resp.status_code == 400, resp.content


def test_rejects_unknown_display_option_key(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(owner)
    resp = api.post(
        _create_url(env.id),
        _body(env.id, survey_type="app", display_options={"frequency": 5}),
        format="json",
    )
    assert resp.status_code == 400, resp.content


def test_put_leaves_app_fields_when_omitted(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    action = ActionClass.objects.create(environment=env, name="act", type="code")
    api.force_authenticate(owner)
    # Create an app survey.
    create = api.post(
        _create_url(env.id),
        _body(env.id, survey_type="app", triggers=[str(action.id)], display_options={"displayLimit": 2}),
        format="json",
    )
    sid = create.json()["id"]
    # PUT without the app fields → they must be preserved (additive PUT semantics).
    put = api.put(_detail_url(sid), _body(env.id, name="Renamed"), format="json")
    assert put.status_code == 200, put.content
    body = put.json()
    assert body["name"] == "Renamed"
    assert body["survey_type"] == "app"  # preserved.
    assert body["triggers"] == [str(action.id)]  # preserved.
    assert body["display_options"] == {"displayLimit": 2}  # preserved.


def test_put_can_update_app_fields(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    a1 = ActionClass.objects.create(environment=env, name="a1", type="code")
    a2 = ActionClass.objects.create(environment=env, name="a2", type="code")
    api.force_authenticate(owner)
    create = api.post(
        _create_url(env.id),
        _body(env.id, survey_type="app", triggers=[str(a1.id)]),
        format="json",
    )
    sid = create.json()["id"]
    put = api.put(
        _detail_url(sid),
        _body(env.id, name="S", survey_type="app", triggers=[str(a2.id)]),
        format="json",
    )
    assert put.status_code == 200, put.content
    assert put.json()["triggers"] == [str(a2.id)]
