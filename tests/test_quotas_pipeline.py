"""
Quota ENFORCEMENT pipeline tests — finalize -> condition match -> count -> over-quota routing.

The enforcement hook (apps.responses.public_services._apply_quotas, called inside
finalize_response's transaction) marks a finished response over-quota when it is the (limit+1)th
matching FINISHED response for some quota, recording the routed ending. We assert:

  - the FIRST `limit` matching respondents are accepted (not over-quota); the (limit+1)th IS
    over-quota and is routed to the quota's endingId (the "Nth+1 is over-quota" semantics).
  - a NON-matching respondent is never over-quota, regardless of how full the quota is.
  - a survey with NO quotas leaves every response unchanged (over_quota=False, ending None) —
    the byte-identical guarantee.
  - a quota with NO endingId marks the response over_quota=True with quota_full_ending_id=None
    (the runtime then shows the built-in 'quota full' screen).
  - FIRST matching-and-full quota wins (array order) when two quotas are simultaneously full.
  - the finalize handle payload carries over_quota + quota_full_ending_id for the runtime.
  - re-finalize is idempotent (no double-flip, same state).
  - quota enforcement uses the SAME evaluator as logic (a connector condition matches identically).

Org creation provisions a Postgres schema (DDL) -> transactional_db + force_drop cleanup.
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.responses import public_services
from apps.responses.models import Response
from apps.responses.public_views import finalize_handle_body
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "quotas-pipeline-org"

LANGUAGES = [{"code": "en", "label": "English", "default": True}]
QUESTIONS = [
    {
        "id": "country",
        "type": "singleSelect",
        "headline": {"default": "Country?"},
        "config": {
            "choices": [
                {"id": "us", "label": {"default": "US"}},
                {"id": "uk", "label": {"default": "UK"}},
            ]
        },
    },
    {
        "id": "role",
        "type": "singleSelect",
        "headline": {"default": "Role?"},
        "config": {
            "choices": [
                {"id": "pm", "label": {"default": "PM"}},
                {"id": "eng", "label": {"default": "Eng"}},
            ]
        },
    },
]
ENDINGS = [
    {"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}},
    {"id": "quota_full", "type": "endScreen", "headline": {"default": "Full"}},
    {"id": "quota_full_2", "type": "endScreen", "headline": {"default": "Full 2"}},
]


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup():
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=SLUG):
        org.delete(force_drop=True)


@pytest.fixture
def ctx(transactional_db):
    """Org + production env, tenant schema active. Yields (org, env)."""
    owner = _mk_user("quotas_owner@example.com")
    _cleanup()
    org = create_organization(name="Quotas Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, env
    _cleanup()


def _mk_survey(env, quotas, **kwargs):
    defaults = dict(
        name="Quota survey",
        status=SurveyStatus.PUBLISHED,
        questions=QUESTIONS,
        languages=LANGUAGES,
        endings=ENDINGS,
        quotas=quotas,
    )
    defaults.update(kwargs)
    return Survey.objects.create(environment=env, **defaults)


def _finalize(survey, data):
    """Create a finished-pending response with ``data`` and finalize it (runs quota hook)."""
    response = Response.objects.create(
        survey=survey, data=data, finished=False, language="en"
    )
    return public_services.finalize_response(response=response)


US = {"id": "q_us", "name": "US", "limit": 2,
      "condition": {"leftQuestionId": "country", "operator": "equals", "value": "us"},
      "endingId": "quota_full"}


# --------------------------------------------------------------------------- #
#  Nth+1 over-quota routing                                                    #
# --------------------------------------------------------------------------- #
def test_nth_plus_one_matching_is_over_quota_routed_to_ending(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, quotas=[US])  # limit 2 US

    r1 = _finalize(survey, {"country": "us"})
    r2 = _finalize(survey, {"country": "us"})
    r3 = _finalize(survey, {"country": "us"})  # the 3rd US == (limit+1) -> over-quota

    # First `limit` matching respondents are accepted.
    assert r1.over_quota is False and r1.quota_full_ending_id is None
    assert r2.over_quota is False and r2.quota_full_ending_id is None
    # The (limit+1)th matching respondent is over-quota, routed to the quota's ending.
    assert r3.over_quota is True
    assert r3.quota_full_ending_id == "quota_full"
    # And it persisted.
    assert Response.objects.get(pk=r3.pk).over_quota is True


def test_non_matching_respondent_never_over_quota(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, quotas=[US])

    _finalize(survey, {"country": "us"})
    _finalize(survey, {"country": "us"})
    # The US quota is now full, but a UK respondent doesn't match it.
    uk = _finalize(survey, {"country": "uk"})
    assert uk.over_quota is False
    assert uk.quota_full_ending_id is None
    # And a further US respondent IS over-quota (the UK one didn't count toward the US cap).
    us3 = _finalize(survey, {"country": "us"})
    assert us3.over_quota is True


def test_no_quotas_leaves_responses_unchanged(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, quotas=[])

    for _ in range(5):
        r = _finalize(survey, {"country": "us"})
        assert r.over_quota is False
        assert r.quota_full_ending_id is None
        assert r.finished is True


def test_quota_without_ending_uses_builtin_screen(ctx):
    org, env = ctx
    connection.set_tenant(org)
    no_ending = {"id": "q_us", "name": "US", "limit": 1,
                 "condition": {"leftQuestionId": "country", "operator": "equals", "value": "us"}}
    survey = _mk_survey(env, quotas=[no_ending])

    _finalize(survey, {"country": "us"})  # fills limit 1
    over = _finalize(survey, {"country": "us"})  # 2nd US -> over-quota, no endingId
    assert over.over_quota is True
    assert over.quota_full_ending_id is None  # runtime shows the built-in 'quota full' screen


def test_first_matching_full_quota_wins(ctx):
    org, env = ctx
    connection.set_tenant(org)
    # Two quotas a respondent could match simultaneously (country=us AND role=pm). Both limit 1.
    q_us = {"id": "q_us", "name": "US", "limit": 1,
            "condition": {"leftQuestionId": "country", "operator": "equals", "value": "us"},
            "endingId": "quota_full"}
    q_pm = {"id": "q_pm", "name": "PM", "limit": 1,
            "condition": {"leftQuestionId": "role", "operator": "equals", "value": "pm"},
            "endingId": "quota_full_2"}
    survey = _mk_survey(env, quotas=[q_us, q_pm])  # array order: US first

    # Fill both quotas with one matching respondent each.
    _finalize(survey, {"country": "us", "role": "pm"})
    # The 2nd respondent matches BOTH full quotas -> the FIRST in array order (US) wins.
    over = _finalize(survey, {"country": "us", "role": "pm"})
    assert over.over_quota is True
    assert over.quota_full_ending_id == "quota_full"  # q_us's ending (first), not q_pm's


def test_finalize_handle_payload_carries_quota_state(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, quotas=[{**US, "limit": 1}])

    _finalize(survey, {"country": "us"})  # fills limit 1
    over = _finalize(survey, {"country": "us"})  # over-quota
    body = finalize_handle_body(over)
    assert body["over_quota"] is True
    assert body["quota_full_ending_id"] == "quota_full"

    # A non-over-quota finalize body OMITS the quota keys entirely (byte-identical to the
    # pre-quota handle for surveys with no quotas): just {id, finished, edit_token, expires_at}.
    survey2 = _mk_survey(env, quotas=[], name="no quotas")
    normal = _finalize(survey2, {"country": "us"})
    body2 = finalize_handle_body(normal)
    assert set(body2) == {"id", "finished", "edit_token", "expires_at"}


def test_re_finalize_is_idempotent(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, quotas=[{**US, "limit": 1}])

    _finalize(survey, {"country": "us"})  # fills limit 1
    over = _finalize(survey, {"country": "us"})  # over-quota
    assert over.over_quota is True
    # Re-finalize: finalize_response early-returns on an already-finished response, so the state
    # is unchanged (no double-count, no flip).
    again = public_services.finalize_response(response=over)
    assert again.over_quota is True
    assert again.quota_full_ending_id == "quota_full"


def test_connector_condition_matches_same_as_logic(ctx):
    org, env = ctx
    connection.set_tenant(org)
    # AND-condition: US PMs only. Reuses the SAME evaluator as logic (_eval_condition).
    q_us_pm = {
        "id": "q_us_pm", "name": "US PMs", "limit": 1,
        "condition": {
            "connector": "and",
            "conditions": [
                {"leftQuestionId": "country", "operator": "equals", "value": "us"},
                {"leftQuestionId": "role", "operator": "equals", "value": "pm"},
            ],
        },
        "endingId": "quota_full",
    }
    survey = _mk_survey(env, quotas=[q_us_pm])

    # A US Eng does NOT match (role != pm) -> never counts, never over-quota.
    eng = _finalize(survey, {"country": "us", "role": "eng"})
    assert eng.over_quota is False

    # First US PM fills the limit-1 quota; second US PM is over-quota.
    _finalize(survey, {"country": "us", "role": "pm"})
    over = _finalize(survey, {"country": "us", "role": "pm"})
    assert over.over_quota is True
    assert over.quota_full_ending_id == "quota_full"


def test_only_finished_responses_count(ctx):
    org, env = ctx
    connection.set_tenant(org)
    survey = _mk_survey(env, quotas=[{**US, "limit": 1}])

    # An UNFINISHED matching response does not count toward the quota.
    Response.objects.create(survey=survey, data={"country": "us"}, finished=False, language="en")
    # So the first FINISHED US respondent fills (not exceeds) the limit-1 quota.
    r1 = _finalize(survey, {"country": "us"})
    assert r1.over_quota is False
    # The next finished US respondent is over-quota.
    r2 = _finalize(survey, {"country": "us"})
    assert r2.over_quota is True
