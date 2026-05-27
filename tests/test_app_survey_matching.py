"""
Tests for the PUBLIC widget APIs + the app-survey matching engine (FEATURE_PARITY WS-11).

DISTINCT DB: DATABASE_URL=postgres://opensurvey:opensurvey@localhost:5432/parity_appsurvey

Covers the canonical matching rules (apps.actions.public_services):
  - track records (upserts) the person + returns the app-surveys triggered by the action.
  - trigger match: only surveys whose `triggers` include the resolved ActionClass id show.
  - displayLimit cap: after N displays of a survey, it stops showing to that user.
  - recontact cooldown: Workspace.recontact_days blocks ALL app surveys for a window after a
    display of any app survey.
  - displayPercentage gate: the random gate is monkeypatched deterministically.
  - link surveys are NEVER returned (type defaults link; the new endpoints only touch type=app).
  - GET app-surveys returns the catalog (no action filter), gated by user-state.

Real request path through TenantSlugMiddleware; the widget client uses APIClient() WITHOUT
force_authenticate (these endpoints are anonymous). `transactional_db` + `force_drop` for the
schema-provisioning DDL (mirrors tests/test_public_runtime_api.py). Throttle cache cleared
between tests.
"""
import pytest
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from apps.actions.models import ActionClass
from apps.contacts.models import Contact
from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.responses.models import Display
from apps.surveys.enums import SurveyStatus, SurveyType
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

SLUG = "appmatch-org"

QUESTIONS = [
    {
        "id": "q1",
        "type": "openText",
        "headline": {"default": "Feedback?"},
        "required": False,
        "config": {"inputType": "short"},
    }
]
LANGUAGES = [{"code": "default", "label": "Default", "default": True}]


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def env_ctx(transactional_db):
    """Org + workspace + production env. Yields (org, workspace, env). recontact_days=None."""
    connection.set_schema_to_public()
    from django.contrib.auth import get_user_model

    User = get_user_model()
    User.objects.filter(email="appmatch_owner@example.com").delete()
    owner = User.objects.create_user(
        email="appmatch_owner@example.com", password="x", is_verified=True
    )
    _cleanup(SLUG)
    org = create_organization(name="AppMatch Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, ws, env
    _cleanup(SLUG)


def _mk_action(env, name, key=None):
    return ActionClass.objects.create(
        environment=env, name=name, type="code", config={"key": key} if key else {}
    )


def _mk_app_survey(env, name, triggers, *, status=SurveyStatus.PUBLISHED, display_options=None):
    return Survey.objects.create(
        environment=env,
        name=name,
        status=status,
        survey_type=SurveyType.APP,
        questions=QUESTIONS,
        languages=LANGUAGES,
        triggers=triggers,
        display_options=display_options or {},
    )


def _mk_link_survey(env, name, *, status=SurveyStatus.PUBLISHED):
    # A normal link survey (default type). It must NEVER appear in app-survey results.
    return Survey.objects.create(
        environment=env,
        name=name,
        status=status,
        survey_type=SurveyType.LINK,
        questions=QUESTIONS,
        languages=LANGUAGES,
    )


def _track_url(env_id):
    return f"/api/v1/{SLUG}/public/environments/{env_id}/actions/track"


def _appsurveys_url(env_id, user_id):
    return f"/api/v1/{SLUG}/public/environments/{env_id}/app-surveys?userId={user_id}"


def _display_url(env_id, survey_id):
    return f"/api/v1/{SLUG}/public/environments/{env_id}/app-surveys/{survey_id}/displays"


# --------------------------------------------------------------------------- #
#  track — records the person + returns triggered surveys
# --------------------------------------------------------------------------- #
def test_track_records_person_and_returns_triggered_survey(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "Upgrade Clicked", key="upgrade")
    survey = _mk_app_survey(env, "Upgrade NPS", triggers=[str(action.id)])

    resp = api.post(
        _track_url(env.id), {"userId": "user-123", "action": "upgrade"}, format="json"
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["matched_action"] is True
    assert [s["id"] for s in body["surveys"]] == [str(survey.id)]
    # Person upserted as a Contact keyed by external_id.
    connection.set_tenant(org)
    assert Contact.objects.filter(environment=env, external_id="user-123").exists()
    assert body["person_id"]  # echoed contact id.


def test_track_by_name_when_no_key(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "signup_done")  # no config.key → matched by name.
    survey = _mk_app_survey(env, "Welcome", triggers=[str(action.id)])
    resp = api.post(
        _track_url(env.id), {"userId": "u1", "action": "signup_done"}, format="json"
    )
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["surveys"]] == [str(survey.id)]


def test_track_unknown_action_is_200_empty(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    _mk_app_survey(env, "S", triggers=[])
    resp = api.post(
        _track_url(env.id), {"userId": "u1", "action": "never_defined"}, format="json"
    )
    assert resp.status_code == 200
    assert resp.json()["matched_action"] is False
    assert resp.json()["surveys"] == []


def test_track_does_not_return_survey_for_other_action(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    a1 = _mk_action(env, "action_one", key="one")
    _mk_action(env, "action_two", key="two")  # the action we track; resolves but triggers nothing.
    _mk_app_survey(env, "Triggered by one", triggers=[str(a1.id)])
    # Track action_two → survey (triggered by one) must NOT appear.
    resp = api.post(_track_url(env.id), {"userId": "u1", "action": "two"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["surveys"] == []
    assert resp.json()["matched_action"] is True  # action_two DID resolve, just no survey.


def test_track_does_not_create_displays(api, env_ctx):
    """track returns surveys but NEVER records a Display (the widget does that per shown survey)."""
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "act", key="k")
    _mk_app_survey(env, "S", triggers=[str(action.id)])
    api.post(_track_url(env.id), {"userId": "u1", "action": "k"}, format="json")
    connection.set_tenant(org)
    assert Display.objects.count() == 0


# --------------------------------------------------------------------------- #
#  Link surveys are never returned
# --------------------------------------------------------------------------- #
def test_link_survey_never_appears_in_track(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "act", key="k")
    # A LINK survey that (illegally) lists the action in triggers — must still be ignored.
    link = _mk_link_survey(env, "Link Survey")
    link.triggers = [str(action.id)]
    link.save(update_fields=["triggers"])
    resp = api.post(_track_url(env.id), {"userId": "u1", "action": "k"}, format="json")
    assert resp.status_code == 200
    returned = [s["id"] for s in resp.json()["surveys"]]
    assert str(link.id) not in returned


def test_link_survey_never_appears_in_app_surveys(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    _mk_link_survey(env, "Link Survey")
    resp = api.get(_appsurveys_url(env.id, "u1"))
    assert resp.status_code == 200
    assert resp.json()["surveys"] == []


def test_unpublished_app_survey_not_returned(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "act", key="k")
    _mk_app_survey(env, "Draft", triggers=[str(action.id)], status=SurveyStatus.DRAFT)
    resp = api.post(_track_url(env.id), {"userId": "u1", "action": "k"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["surveys"] == []


# --------------------------------------------------------------------------- #
#  displayLimit cap
# --------------------------------------------------------------------------- #
def test_display_limit_caps_per_user(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "act", key="k")
    survey = _mk_app_survey(env, "Capped", triggers=[str(action.id)], display_options={"displayLimit": 2})

    # Simulate the widget having shown the survey twice to this user (2 Displays).
    contact = Contact.objects.create(environment=env, external_id="capped-user")
    Display.objects.create(survey=survey, contact_id=contact.id)
    Display.objects.create(survey=survey, contact_id=contact.id)

    resp = api.post(
        _track_url(env.id), {"userId": "capped-user", "action": "k"}, format="json"
    )
    assert resp.status_code == 200
    assert resp.json()["surveys"] == []  # limit reached → not shown.


def test_display_limit_allows_below_cap(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "act", key="k")
    survey = _mk_app_survey(env, "Capped", triggers=[str(action.id)], display_options={"displayLimit": 2})
    contact = Contact.objects.create(environment=env, external_id="below-user")
    Display.objects.create(survey=survey, contact_id=contact.id)  # only 1 < 2.
    resp = api.post(
        _track_url(env.id), {"userId": "below-user", "action": "k"}, format="json"
    )
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["surveys"]] == [str(survey.id)]


def test_display_limit_via_http_display_endpoint(api, env_ctx):
    """END-TO-END through the HTTP display endpoint (the PRODUCTION flow): track returns a survey,
    the widget records each show via POST .../displays (userId-keyed), and once the limit is hit
    the next track no longer returns it. This proves displayLimit fires in production (the widget
    has NO link_token, so the display MUST be keyed by userId — which the dedicated endpoint does).
    """
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "act", key="k")
    survey = _mk_app_survey(
        env, "Capped", triggers=[str(action.id)], display_options={"displayLimit": 2}
    )
    user = "http-cap-user"

    # 1st track → survey eligible.
    r = api.post(_track_url(env.id), {"userId": user, "action": "k"}, format="json")
    assert [s["id"] for s in r.json()["surveys"]] == [str(survey.id)]
    # Widget shows it → records a display via the HTTP endpoint (userId-keyed).
    d1 = api.post(_display_url(env.id, survey.id), {"userId": user}, format="json")
    assert d1.status_code == 201, d1.content
    assert d1.json()["person_id"]  # the Display is tied to a Contact (NOT anonymous).

    # 2nd track → still eligible (1 < 2) → show again → 2nd display.
    r = api.post(_track_url(env.id), {"userId": user, "action": "k"}, format="json")
    assert [s["id"] for s in r.json()["surveys"]] == [str(survey.id)]
    d2 = api.post(_display_url(env.id, survey.id), {"userId": user}, format="json")
    assert d2.status_code == 201

    # 3rd track → limit (2) reached → survey NO LONGER returned.
    r = api.post(_track_url(env.id), {"userId": user, "action": "k"}, format="json")
    assert r.json()["surveys"] == []

    # The displays were keyed to the user's Contact (the production-flow assertion).
    connection.set_tenant(org)
    contact = Contact.objects.get(environment=env, external_id=user)
    assert Display.objects.filter(survey=survey, contact_id=contact.id).count() == 2
    assert Display.objects.filter(survey=survey, contact_id__isnull=True).count() == 0


def test_display_endpoint_rejects_link_survey(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    link = _mk_link_survey(env, "Link")
    r = api.post(_display_url(env.id, link.id), {"userId": "u1"}, format="json")
    assert r.status_code == 404  # not an app survey → no impression.


def test_display_endpoint_rejects_unpublished(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    draft = _mk_app_survey(env, "Draft", triggers=[], status=SurveyStatus.DRAFT)
    r = api.post(_display_url(env.id, draft.id), {"userId": "u1"}, format="json")
    assert r.status_code == 404


def test_recontact_cooldown_via_http_display(api, env_ctx):
    """Cooldown fires through the production display flow: a userId-keyed HTTP display of survey A
    suppresses survey B on the next track (cross-survey, workspace-level cooldown)."""
    org, ws, env = env_ctx
    connection.set_tenant(org)
    ws.recontact_days = 7
    ws.save(update_fields=["recontact_days"])
    action = _mk_action(env, "act", key="k")
    survey_a = _mk_app_survey(env, "A", triggers=[str(action.id)])
    _mk_app_survey(env, "B", triggers=[str(action.id)])
    user = "http-cooldown-user"

    # Track → both eligible; show A via the HTTP display endpoint.
    r = api.post(_track_url(env.id), {"userId": user, "action": "k"}, format="json")
    assert {s["id"] for s in r.json()["surveys"]}  # non-empty.
    api.post(_display_url(env.id, survey_a.id), {"userId": user}, format="json")

    # Next track → cooldown active → NOTHING shows (A and B both suppressed).
    r = api.post(_track_url(env.id), {"userId": user, "action": "k"}, format="json")
    assert r.json()["surveys"] == []


# --------------------------------------------------------------------------- #
#  Recontact cooldown (workspace-level, cross-survey)
# --------------------------------------------------------------------------- #
def test_recontact_cooldown_blocks_all_app_surveys(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    ws.recontact_days = 7
    ws.save(update_fields=["recontact_days"])
    action = _mk_action(env, "act", key="k")
    survey_a = _mk_app_survey(env, "A", triggers=[str(action.id)])
    _mk_app_survey(env, "B", triggers=[str(action.id)])  # also suppressed by the cross-survey cooldown.

    # The user was shown survey_a recently (within the 7-day window).
    contact = Contact.objects.create(environment=env, external_id="cooldown-user")
    Display.objects.create(survey=survey_a, contact_id=contact.id)  # shown_at = now.

    resp = api.post(
        _track_url(env.id), {"userId": "cooldown-user", "action": "k"}, format="json"
    )
    assert resp.status_code == 200
    # Cooldown active → NO app survey shows (cross-survey: both A and B suppressed).
    assert resp.json()["surveys"] == []


def test_recontact_cooldown_expires(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    ws.recontact_days = 7
    ws.save(update_fields=["recontact_days"])
    action = _mk_action(env, "act", key="k")
    survey = _mk_app_survey(env, "A", triggers=[str(action.id)])
    contact = Contact.objects.create(environment=env, external_id="expired-user")
    d = Display.objects.create(survey=survey, contact_id=contact.id)
    # Backdate the display beyond the cooldown window (8 days ago > 7-day cooldown).
    Display.objects.filter(pk=d.id).update(
        shown_at=timezone.now() - timezone.timedelta(days=8)
    )
    resp = api.post(
        _track_url(env.id), {"userId": "expired-user", "action": "k"}, format="json"
    )
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["surveys"]] == [str(survey.id)]


def test_no_cooldown_when_recontact_days_null(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    assert ws.recontact_days is None  # default: no cooldown.
    action = _mk_action(env, "act", key="k")
    survey_a = _mk_app_survey(env, "A", triggers=[str(action.id)])
    survey_b = _mk_app_survey(env, "B", triggers=[str(action.id)])
    contact = Contact.objects.create(environment=env, external_id="nocool-user")
    Display.objects.create(survey=survey_a, contact_id=contact.id)  # recent display, no cooldown.
    resp = api.post(
        _track_url(env.id), {"userId": "nocool-user", "action": "k"}, format="json"
    )
    assert resp.status_code == 200
    returned = {s["id"] for s in resp.json()["surveys"]}
    # Both still eligible (no cooldown configured).
    assert returned == {str(survey_a.id), str(survey_b.id)}


# --------------------------------------------------------------------------- #
#  displayPercentage gate (monkeypatched deterministic)
# --------------------------------------------------------------------------- #
def test_percentage_gate_blocks_when_false(api, env_ctx, monkeypatch):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "act", key="k")
    _mk_app_survey(env, "Gated", triggers=[str(action.id)], display_options={"displayPercentage": 50})
    # Force the gate to FAIL deterministically.
    monkeypatch.setattr("apps.actions.public_services._passes_percentage_gate", lambda pct: False)
    resp = api.post(_track_url(env.id), {"userId": "u1", "action": "k"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["surveys"] == []


def test_percentage_gate_passes_when_true(api, env_ctx, monkeypatch):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    action = _mk_action(env, "act", key="k")
    survey = _mk_app_survey(env, "Gated", triggers=[str(action.id)], display_options={"displayPercentage": 50})
    monkeypatch.setattr("apps.actions.public_services._passes_percentage_gate", lambda pct: True)
    resp = api.post(_track_url(env.id), {"userId": "u1", "action": "k"}, format="json")
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["surveys"]] == [str(survey.id)]


# --------------------------------------------------------------------------- #
#  GET app-surveys (catalog; no action filter)
# --------------------------------------------------------------------------- #
def test_app_surveys_returns_catalog(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    a = _mk_action(env, "act", key="k")
    s1 = _mk_app_survey(env, "S1", triggers=[str(a.id)])
    s2 = _mk_app_survey(env, "S2", triggers=[])  # no triggers — still in the catalog.
    resp = api.get(_appsurveys_url(env.id, "u1"))
    assert resp.status_code == 200
    returned = {s["id"] for s in resp.json()["surveys"]}
    assert returned == {str(s1.id), str(s2.id)}
    # Each item carries triggers + display_options for the widget to fire client-side.
    item = next(s for s in resp.json()["surveys"] if s["id"] == str(s1.id))
    assert item["triggers"] == [str(a.id)]
    assert "display_options" in item


def test_app_surveys_requires_user_id(api, env_ctx):
    org, ws, env = env_ctx
    resp = api.get(f"/api/v1/{SLUG}/public/environments/{env.id}/app-surveys")
    assert resp.status_code == 400


def test_app_surveys_respects_cooldown(api, env_ctx):
    org, ws, env = env_ctx
    connection.set_tenant(org)
    ws.recontact_days = 30
    ws.save(update_fields=["recontact_days"])
    s = _mk_app_survey(env, "S", triggers=[])
    contact = Contact.objects.create(environment=env, external_id="u1")
    Display.objects.create(survey=s, contact_id=contact.id)  # recent → cooldown active.
    resp = api.get(_appsurveys_url(env.id, "u1"))
    assert resp.status_code == 200
    assert resp.json()["surveys"] == []
