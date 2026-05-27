"""
Tests for the deepened Results / Summary analytics (apps/surveys/analytics.py + the wired
``survey_results`` HTMX view).

Two layers:

  * UNIT — pure-Python folds over hand-built ``Survey`` + ``Response`` rows (NO HTTP/RBAC). These
    pin the load-bearing behaviour the brief calls out:
      - completion funnel reached/answered/drop-off, BRANCH-AWARE (jumpTo skips a question →
        that question is neither reached nor a drop-off; a `skip` action removes a question;
        a partial response abandons at a step → reached=1, answered=0 there).
      - completion rate.
      - time-to-complete (avg + median) from started_at/finished_at over finished responses.
      - per-type summaries for EVERY type (nps/rating/choice/picture/ranking/matrix/number/
        date/file/consent/cta/meeting/composite/openText).
      - responses-over-time dense series.

  * INTEGRATION — the real ``survey_results`` view renders the new sections (funnel, activity
    sparkline, time-to-complete KPI, the newer per-type blocks) and stays N+1-free (a bounded
    query count regardless of question/response count).

Uses ``transactional_db`` (tenant schema DDL) mirroring tests/test_surveys_admin.py. Run on a
DISTINCT DB:  DATABASE_URL=postgres://opensurvey:opensurvey@localhost:5432/parity_analytics
"""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.utils import timezone

from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.responses.models import Response
from apps.surveys import analytics
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()
SLUG = "analytics-org"


# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return Client()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def org_env(transactional_db):
    """Org + owner + a single workspace/prod env. Yields (org, owner, env)."""
    owner = _mk_user("analytics_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Analytics Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="Main WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
    _cleanup(SLUG)


def _mk_survey(env, questions, *, endings=None, status=SurveyStatus.PUBLISHED):
    return Survey.objects.create(
        environment=env,
        name="Analytics Survey",
        status=status,
        questions=questions,
        languages=[{"code": "en", "label": "English", "default": True}],
        styling={},
        endings=endings or [],
    )


def _set_times(resp, started, finished):
    """started_at is auto_now_add — bypass it with a direct UPDATE."""
    Response.objects.filter(pk=resp.pk).update(started_at=started, finished_at=finished)


def _u(path):
    return f"/{SLUG}{path}"


# Linear 4-question survey (no logic) for the simplest funnel asserts.
_LINEAR_Q = [
    {"id": "q1", "type": "openText", "headline": {"default": "Q1"}, "config": {}},
    {"id": "q2", "type": "openText", "headline": {"default": "Q2"}, "config": {}},
    {"id": "q3", "type": "openText", "headline": {"default": "Q3"}, "config": {}},
    {"id": "q4", "type": "openText", "headline": {"default": "Q4"}, "config": {}},
]


# --------------------------------------------------------------------------- #
#  Completion rate                                                             #
# --------------------------------------------------------------------------- #
def test_completion_rate_basic():
    assert analytics.completion_rate(0, 0) is None
    assert analytics.completion_rate(10, 5) == 50
    assert analytics.completion_rate(3, 1) == 33  # 33.33 → 33


# --------------------------------------------------------------------------- #
#  Funnel — linear, abandonment                                               #
# --------------------------------------------------------------------------- #
def test_funnel_linear_reached_answered_dropoff(org_env):
    org, owner, env = org_env
    survey = _mk_survey(env, _LINEAR_Q)
    # R1 finished all 4. R2 reached q3 (answered q1,q2) and ABANDONED at q3 (no q3 answer).
    Response.objects.create(survey=survey, finished=True,
                            data={"q1": "a", "q2": "b", "q3": "c", "q4": "d"})
    Response.objects.create(survey=survey, finished=False, data={"q1": "a", "q2": "b"})
    responses = list(Response.objects.filter(survey=survey))

    funnel = {row["id"]: row for row in analytics.completion_funnel(survey, responses)}

    # Both started → both reach q1, q2.
    assert funnel["q1"]["reached"] == 2
    assert funnel["q2"]["reached"] == 2
    assert funnel["q1"]["answered"] == 2
    assert funnel["q2"]["answered"] == 2
    # q3 is REACHED by both (R2's path advanced to q3 even though q3 unanswered)...
    assert funnel["q3"]["reached"] == 2
    # ...but only R1 ANSWERED q3 → R2 is the abandonment signal (reached, not answered).
    assert funnel["q3"]["answered"] == 1
    # q4: only R1 reached it (R2 abandoned at q3 → never progressed past it).
    assert funnel["q4"]["reached"] == 1
    # Drop-off INTO q4 = reached(q3) - reached(q4) = 2 - 1 = 1 (R2 dropped there).
    assert funnel["q4"]["drop_off"] == 1
    # Percentages are of the 2 who started.
    assert funnel["q4"]["reached_pct"] == 50


def test_funnel_partial_abandons_first_question(org_env):
    org, owner, env = org_env
    survey = _mk_survey(env, _LINEAR_Q)
    # A response that reached q1 but answered nothing (opened, bounced).
    Response.objects.create(survey=survey, finished=False, data={})
    responses = list(Response.objects.filter(survey=survey))
    funnel = {row["id"]: row for row in analytics.completion_funnel(survey, responses)}
    assert funnel["q1"]["reached"] == 1
    assert funnel["q1"]["answered"] == 0
    # Nobody progressed past q1.
    assert funnel["q2"]["reached"] == 0
    assert funnel["q2"]["drop_off"] == 1


def test_funnel_empty_survey_returns_empty():
    survey = Survey(questions=[], endings=[])
    assert analytics.completion_funnel(survey, []) == []


# --------------------------------------------------------------------------- #
#  Funnel — BRANCHING (jumpTo + skip): the load-bearing correctness case       #
# --------------------------------------------------------------------------- #
def test_funnel_jumpto_bypasses_question(org_env):
    """A jumpTo from q1 → q3 (skipping q2) means q2 is NEITHER reached NOR a drop-off for the
    branching respondent: it was branched past, not abandoned."""
    org, owner, env = org_env
    questions = [
        {
            "id": "q1", "type": "singleSelect", "headline": {"default": "Path?"},
            "config": {"choices": [{"id": "skip", "label": {"default": "Skip ahead"}},
                                   {"id": "stay", "label": {"default": "Stay"}}]},
            "logic": [
                {"id": "r1",
                 "condition": {"leftQuestionId": "q1", "operator": "equals", "value": "skip"},
                 "action": {"type": "jumpTo", "target": "q3"}},
            ],
        },
        {"id": "q2", "type": "openText", "headline": {"default": "Q2"}, "config": {}},
        {"id": "q3", "type": "openText", "headline": {"default": "Q3"}, "config": {}},
    ]
    survey = _mk_survey(env, questions)
    # R1 chose "skip" → path q1 → q3 (q2 bypassed). R2 chose "stay" → q1 → q2 → q3.
    Response.objects.create(survey=survey, finished=True, data={"q1": "skip", "q3": "done"})
    Response.objects.create(survey=survey, finished=True,
                            data={"q1": "stay", "q2": "mid", "q3": "done"})
    responses = list(Response.objects.filter(survey=survey))
    funnel = {row["id"]: row for row in analytics.completion_funnel(survey, responses)}

    assert funnel["q1"]["reached"] == 2
    # q2 reached only by R2 (R1 jumped past it).
    assert funnel["q2"]["reached"] == 1
    # q3 reached by BOTH (R1 via jump, R2 linearly).
    assert funnel["q3"]["reached"] == 2
    assert funnel["q3"]["answered"] == 2


def test_funnel_skip_action_removes_question(org_env):
    """A `skip` action on q1 drops q2 from the path for the matching respondent."""
    org, owner, env = org_env
    questions = [
        {
            "id": "q1", "type": "singleSelect", "headline": {"default": "Hide Q2?"},
            "config": {"choices": [{"id": "yes", "label": {"default": "Yes"}},
                                   {"id": "no", "label": {"default": "No"}}]},
            "logic": [
                {"id": "r1",
                 "condition": {"leftQuestionId": "q1", "operator": "equals", "value": "yes"},
                 "action": {"type": "skip", "target": "q2"}},
            ],
        },
        {"id": "q2", "type": "openText", "headline": {"default": "Q2"}, "config": {}},
        {"id": "q3", "type": "openText", "headline": {"default": "Q3"}, "config": {}},
    ]
    survey = _mk_survey(env, questions)
    Response.objects.create(survey=survey, finished=True, data={"q1": "yes", "q3": "end"})
    responses = list(Response.objects.filter(survey=survey))
    funnel = {row["id"]: row for row in analytics.completion_funnel(survey, responses)}
    # q2 was skipped → never reached, and NOT counted as a drop-off.
    assert funnel["q2"]["reached"] == 0
    assert funnel["q2"]["drop_off"] == 0
    assert funnel["q3"]["reached"] == 1


def test_funnel_branch_plus_abandon_combined(org_env):
    """Combined: a jumpTo q1→q3 branch + a partial that took the NON-jump path and abandoned at
    q2. Pins both the truncation rule (partial drops at q2, not q3) and branch-awareness."""
    org, owner, env = org_env
    questions = [
        {
            "id": "q1", "type": "singleSelect", "headline": {"default": "Jump?"},
            "config": {"choices": [{"id": "jump", "label": {"default": "Jump"}},
                                   {"id": "stay", "label": {"default": "Stay"}}]},
            "logic": [
                {"id": "r1",
                 "condition": {"leftQuestionId": "q1", "operator": "equals", "value": "jump"},
                 "action": {"type": "jumpTo", "target": "q3"}},
            ],
        },
        {"id": "q2", "type": "openText", "headline": {"default": "Q2"}, "config": {}},
        {"id": "q3", "type": "openText", "headline": {"default": "Q3"}, "config": {}},
    ]
    survey = _mk_survey(env, questions)
    # R1 jumped (q1=jump → q3) and finished. R2 chose "stay" and ABANDONED at q2 (no q2 answer).
    Response.objects.create(survey=survey, finished=True, data={"q1": "jump", "q3": "done"})
    Response.objects.create(survey=survey, finished=False, data={"q1": "stay"})
    responses = list(Response.objects.filter(survey=survey))
    funnel = {row["id"]: row for row in analytics.completion_funnel(survey, responses)}

    # CONVENTION: drop_off attributes to the step a respondent did NOT reach; the bail point
    # itself reads as reached > answered (not as a drop_off on that same question).
    assert funnel["q1"]["reached"] == 2
    # R2 reached q2 (the step after their last answer q1) but never answered it → bail point:
    # reached(1) > answered(0). The drop is attributed to the next step (q3) they failed to enter.
    assert funnel["q2"]["reached"] == 1
    assert funnel["q2"]["answered"] == 0
    assert funnel["q2"]["drop_off"] == 0
    # q3 reached only by R1 (the jumper); R2 failed to advance INTO q3 → drop_off here.
    assert funnel["q3"]["reached"] == 1
    assert funnel["q3"]["drop_off"] == 1


# --------------------------------------------------------------------------- #
#  Time-to-complete                                                           #
# --------------------------------------------------------------------------- #
def test_time_to_complete_avg_and_median(org_env):
    org, owner, env = org_env
    survey = _mk_survey(env, _LINEAR_Q)
    base = timezone.now() - timedelta(hours=2)
    # Three finished responses: 60s, 120s, 600s → median 120s, avg 260s.
    durations = [60, 120, 600]
    for i, secs in enumerate(durations):
        r = Response.objects.create(survey=survey, finished=True, data={"q1": str(i)})
        _set_times(r, base, base + timedelta(seconds=secs))
    # One PARTIAL (no finished_at) must be ignored.
    Response.objects.create(survey=survey, finished=False, data={"q1": "x"})
    responses = list(Response.objects.filter(survey=survey))

    ttc = analytics.time_to_complete(responses)
    assert ttc["count"] == 3
    assert ttc["median_seconds"] == 120
    assert round(ttc["avg_seconds"]) == 260
    assert ttc["median_display"] == "2m"
    # avg 260s = 4m 20s
    assert ttc["avg_display"] == "4m 20s"


def test_time_to_complete_none_when_no_finished(org_env):
    org, owner, env = org_env
    survey = _mk_survey(env, _LINEAR_Q)
    Response.objects.create(survey=survey, finished=False, data={"q1": "x"})
    responses = list(Response.objects.filter(survey=survey))
    ttc = analytics.time_to_complete(responses)
    assert ttc["count"] == 0
    assert ttc["avg_display"] is None
    assert ttc["median_display"] is None


def test_fmt_duration_units():
    assert analytics._fmt_duration(None) is None
    assert analytics._fmt_duration(45) == "45s"
    assert analytics._fmt_duration(150) == "2m 30s"
    assert analytics._fmt_duration(120) == "2m"
    assert analytics._fmt_duration(3660) == "1h 1m"
    assert analytics._fmt_duration(3600) == "1h"


# --------------------------------------------------------------------------- #
#  Per-type summaries                                                         #
# --------------------------------------------------------------------------- #
def _summaries_by_id(survey, responses):
    return {e["id"]: e for e in analytics.question_results(survey, responses)}


def test_per_type_nps_rating_choice(org_env):
    org, owner, env = org_env
    questions = [
        {"id": "nps1", "type": "nps", "headline": {"default": "NPS"}, "config": {}},
        {"id": "rate1", "type": "rating", "headline": {"default": "Rate"},
         "config": {"range": 5, "scale": "star"}},
        {"id": "sel1", "type": "singleSelect", "headline": {"default": "Pick"},
         "config": {"choices": [{"id": "a", "label": {"default": "Apple"}},
                                {"id": "b", "label": {"default": "Banana"}}]}},
        {"id": "ms1", "type": "multiSelect", "headline": {"default": "Multi"},
         "config": {"choices": [{"id": "x", "label": {"default": "X"}},
                                {"id": "y", "label": {"default": "Y"}}]}},
    ]
    survey = _mk_survey(env, questions)
    Response.objects.create(survey=survey, finished=True,
                            data={"nps1": 10, "rate1": 5, "sel1": "a", "ms1": ["x", "y"]})
    Response.objects.create(survey=survey, finished=True,
                            data={"nps1": 3, "rate1": 1, "sel1": "a", "ms1": ["x"]})
    responses = list(Response.objects.filter(survey=survey))
    s = _summaries_by_id(survey, responses)

    assert s["nps1"]["kind"] == "nps"
    # promoter(10) - detractor(3) = 50% - 50% = 0
    assert s["nps1"]["nps"]["nps"] == 0
    assert s["rate1"]["kind"] == "rating"
    assert s["rate1"]["average"] == 3.0  # (5+1)/2
    assert s["sel1"]["kind"] == "choice"
    apple = next(r for r in s["sel1"]["rows"] if r["label"] == "Apple")
    assert apple["count"] == 2 and apple["pct"] == 100
    assert s["ms1"]["kind"] == "choice"
    x = next(r for r in s["ms1"]["rows"] if r["label"] == "X")
    assert x["count"] == 2  # both picked x


def test_per_type_picture_ranking_matrix(org_env):
    org, owner, env = org_env
    questions = [
        {"id": "pic1", "type": "pictureChoice", "headline": {"default": "Pic"},
         "config": {"options": [
             {"id": "p1", "imageUrl": "https://x/1.png", "label": {"default": "One"}},
             {"id": "p2", "imageUrl": "https://x/2.png", "label": {"default": "Two"}}]}},
        {"id": "rank1", "type": "ranking", "headline": {"default": "Rank"},
         "config": {"choices": [{"id": "c1", "label": {"default": "First"}},
                                {"id": "c2", "label": {"default": "Second"}}]}},
        {"id": "mtx1", "type": "matrix", "headline": {"default": "Matrix"},
         "config": {"selection": "single",
                    "rows": [{"id": "r1", "label": {"default": "Speed"}}],
                    "columns": [{"id": "good", "label": {"default": "Good"}},
                                {"id": "bad", "label": {"default": "Bad"}}]}},
    ]
    survey = _mk_survey(env, questions)
    Response.objects.create(survey=survey, finished=True, data={
        "pic1": "p1", "rank1": ["c1", "c2"], "mtx1": {"r1": "good"}})
    Response.objects.create(survey=survey, finished=True, data={
        "pic1": "p1", "rank1": ["c2", "c1"], "mtx1": {"r1": "bad"}})
    responses = list(Response.objects.filter(survey=survey))
    s = _summaries_by_id(survey, responses)

    assert s["pic1"]["kind"] == "picture"
    one = next(r for r in s["pic1"]["rows"] if r["label"] == "One")
    assert one["count"] == 2 and one["image_url"] == "https://x/1.png"

    assert s["rank1"]["kind"] == "ranking"
    first = next(r for r in s["rank1"]["rows"] if r["label"] == "First")
    # c1 ranked at positions 1 and 2 → avg rank 1.5; one #1 vote.
    assert first["avg_rank"] == 1.5
    assert first["first_count"] == 1

    assert s["mtx1"]["kind"] == "matrix"
    speed = s["mtx1"]["rows"][0]
    assert speed["total"] == 2
    good = next(c for c in speed["cells"] if c["label"] == "Good")
    assert good["count"] == 1 and good["pct"] == 50


def test_per_type_number_date_file_consent_cta_meeting(org_env):
    org, owner, env = org_env
    questions = [
        {"id": "num1", "type": "number", "headline": {"default": "Age"}, "config": {}},
        {"id": "dt1", "type": "date", "headline": {"default": "When"},
         "config": {"format": "YYYY-MM-DD"}},
        {"id": "file1", "type": "fileUpload", "headline": {"default": "Files"}, "config": {}},
        {"id": "con1", "type": "consent", "headline": {"default": "Agree"},
         "config": {"label": {"default": "I agree"}}},
        {"id": "cta1", "type": "cta", "headline": {"default": "Act"}, "config": {}},
        {"id": "meet1", "type": "scheduleMeeting", "headline": {"default": "Book"},
         "config": {"calLink": "jane/30min"}},
    ]
    survey = _mk_survey(env, questions)
    Response.objects.create(survey=survey, finished=True, data={
        "num1": 30, "dt1": "2026-01-10", "file1": ["https://x/a.pdf", "https://x/b.pdf"],
        "con1": True, "cta1": True, "meet1": {"booked": True, "uid": "u1"}})
    Response.objects.create(survey=survey, finished=True, data={
        "num1": 50, "dt1": "2026-03-20", "con1": False})
    responses = list(Response.objects.filter(survey=survey))
    s = _summaries_by_id(survey, responses)

    assert s["num1"]["kind"] == "number"
    assert s["num1"]["average"] == 40.0
    assert s["num1"]["minimum"] == 30 and s["num1"]["maximum"] == 50

    assert s["dt1"]["kind"] == "date"
    assert s["dt1"]["earliest"] == "2026-01-10" and s["dt1"]["latest"] == "2026-03-20"

    assert s["file1"]["kind"] == "file"
    assert s["file1"]["total_files"] == 2 and s["file1"]["with_files"] == 1

    assert s["con1"]["kind"] == "consent"
    assert s["con1"]["agreed"] == 1 and s["con1"]["declined"] == 1
    assert s["con1"]["agreed_pct"] == 50  # 1 of 2 non-empty consent answers

    assert s["cta1"]["kind"] == "cta"
    assert s["cta1"]["clicked"] == 1

    assert s["meet1"]["kind"] == "meeting"
    assert s["meet1"]["booked"] == 1


def test_per_type_composite_fill_rate(org_env):
    org, owner, env = org_env
    questions = [
        {"id": "ci1", "type": "contactInfo", "headline": {"default": "Contact"},
         "config": {"fields": {"firstName": {"show": True}, "email": {"show": True},
                               "phone": {"show": True}}}},
        {"id": "addr1", "type": "address", "headline": {"default": "Address"},
         "config": {"fields": {"city": {"show": True}, "country": {"show": True}}}},
    ]
    survey = _mk_survey(env, questions)
    Response.objects.create(survey=survey, finished=True, data={
        "ci1": {"firstName": "Ada", "email": "ada@x.io"},  # phone empty
        "addr1": {"city": "Pune", "country": "IN"}})
    Response.objects.create(survey=survey, finished=True, data={
        "ci1": {"firstName": "Lin"},  # only firstName
        "addr1": {"city": "Goa"}})  # country empty
    responses = list(Response.objects.filter(survey=survey))
    s = _summaries_by_id(survey, responses)

    assert s["ci1"]["kind"] == "composite"
    fields = {f["label"]: f for f in s["ci1"]["fields"]}
    assert fields["firstName"]["count"] == 2 and fields["firstName"]["pct"] == 100
    assert fields["email"]["count"] == 1 and fields["email"]["pct"] == 50
    assert fields["phone"]["count"] == 0

    assert s["addr1"]["kind"] == "composite"
    afields = {f["label"]: f for f in s["addr1"]["fields"]}
    assert afields["city"]["count"] == 2
    assert afields["country"]["count"] == 1


def test_per_type_open_text_samples(org_env):
    org, owner, env = org_env
    survey = _mk_survey(env, [
        {"id": "txt1", "type": "openText", "headline": {"default": "Feedback"}, "config": {}}])
    for i in range(7):
        Response.objects.create(survey=survey, finished=True, data={"txt1": f"comment {i}"})
    responses = list(Response.objects.filter(survey=survey))
    s = _summaries_by_id(survey, responses)
    assert s["txt1"]["kind"] == "text"
    assert s["txt1"]["answered_count"] == 7
    assert len(s["txt1"]["samples"]) == 5  # capped at 5


# --------------------------------------------------------------------------- #
#  Responses-over-time                                                        #
# --------------------------------------------------------------------------- #
def test_responses_per_day_dense_series():
    now = timezone.now()
    dates = [now, now, now - timedelta(days=1)]
    series = analytics.responses_per_day(dates, days=7)
    assert len(series) == 7  # zero-filled dense
    # Last day (today) has 2, prior day 1.
    assert series[-1]["count"] == 2
    assert series[-2]["count"] == 1
    assert series[0]["count"] == 0


def test_responses_per_day_empty():
    series = analytics.responses_per_day([], days=7)
    assert len(series) == 7
    assert all(p["count"] == 0 for p in series)


# --------------------------------------------------------------------------- #
#  INTEGRATION — the wired view renders the new sections + stays N+1-free      #
# --------------------------------------------------------------------------- #
def test_results_view_renders_funnel_and_ttc(client, org_env):
    org, owner, env = org_env
    survey = _mk_survey(env, _LINEAR_Q)
    base = timezone.now() - timedelta(hours=1)
    r1 = Response.objects.create(survey=survey, finished=True,
                                 data={"q1": "a", "q2": "b", "q3": "c", "q4": "d"})
    _set_times(r1, base, base + timedelta(seconds=90))
    Response.objects.create(survey=survey, finished=False, data={"q1": "a", "q2": "b"})

    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/results"))
    assert resp.status_code == 200
    html = resp.content.decode()
    # New sections present.
    assert "Completion funnel" in html
    assert "Responses over time" in html
    assert "Median time to complete" in html
    assert "reached" in html and "answered" in html
    # No legacy {# #} comment markers leaked into the rendered page.
    assert "{#" not in html


def test_results_view_renders_all_newer_types(client, org_env):
    org, owner, env = org_env
    questions = [
        {"id": "pic1", "type": "pictureChoice", "headline": {"default": "Pic"},
         "config": {"options": [
             {"id": "p1", "imageUrl": "https://x/1.png", "label": {"default": "One"}},
             {"id": "p2", "imageUrl": "https://x/2.png", "label": {"default": "Two"}}]}},
        {"id": "addr1", "type": "address", "headline": {"default": "Address"},
         "config": {"fields": {"city": {"show": True}}}},
        {"id": "meet1", "type": "scheduleMeeting", "headline": {"default": "Book"},
         "config": {"calLink": "jane"}},
        {"id": "ci1", "type": "contactInfo", "headline": {"default": "Contact"},
         "config": {"fields": {"email": {"show": True}}}},
        {"id": "mtx1", "type": "matrix", "headline": {"default": "Matrix"},
         "config": {"selection": "single",
                    "rows": [{"id": "r1", "label": {"default": "Speed"}}],
                    "columns": [{"id": "g", "label": {"default": "Good"}},
                                {"id": "b", "label": {"default": "Bad"}}]}},
    ]
    survey = _mk_survey(env, questions)
    Response.objects.create(survey=survey, finished=True, data={
        "pic1": "p1", "addr1": {"city": "Pune"}, "meet1": {"booked": True},
        "ci1": {"email": "a@x.io"}, "mtx1": {"r1": "g"}})

    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/results"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Matrix" in html and "Good" in html  # matrix table rendered
    assert "meeting" in html.lower()  # scheduleMeeting block
    assert "Fill rate per field" in html  # composite block (address/contactInfo)
    assert "https://x/1.png" in html  # pictureChoice image


def test_results_view_query_count_bounded(client, org_env, django_assert_max_num_queries):
    """The view must aggregate in a BOUNDED number of queries regardless of how many questions
    or responses exist (no per-question or per-response fan-out)."""
    org, owner, env = org_env
    # 6 questions, 25 responses.
    questions = [{"id": f"q{i}", "type": "openText", "headline": {"default": f"Q{i}"},
                  "config": {}} for i in range(6)]
    survey = _mk_survey(env, questions)
    for i in range(25):
        finished = i % 2 == 0
        Response.objects.create(survey=survey, finished=finished,
                                data={f"q{j}": f"v{i}{j}" for j in range(i % 6 + 1)})

    client.force_login(owner)
    # RBAC + session + the single responses load + survey load + audit-free read path. A generous
    # but FIXED ceiling that does NOT scale with questions/responses — the assert fails loudly if
    # someone reintroduces a per-question query.
    with django_assert_max_num_queries(25):
        resp = client.get(_u(f"/surveys/{survey.id}/results"))
    assert resp.status_code == 200
