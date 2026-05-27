"""
HTTP-level tests for the PUBLIC survey runtime (Wave 3a — openapi "Public Survey" tag).

Covers the unauthenticated, rate-limited take-a-survey loop:
  - GET public survey definition: 200 (published) + PublicSurvey shape, 404 (unpublished/
    unknown), 410 (consumed/expired single-use link), camelCase `welcomeCard`, no-auth-leak.
  - create → patch → finalize via tokens (ADR-009 edit_token; bad/expired token → 403).
  - rate-limit enforcement (per-IP/per-survey throttle → 429).
  - single-use link consumption (token consumed on finalize; reuse blocked).
  - contact derived from link_token, NOT a client-supplied contact_id (reconciled D-fix).
  - Display recorded (POST /displays) + funnel association.
  - the HTML host page renders + mounts the runtime island; deferred upload-url is unrouted.

Real request path through TenantSlugMiddleware; the public client uses APIClient() WITHOUT
force_authenticate (these endpoints are anonymous). `transactional_db` + `force_drop` for the
schema-provisioning DDL (mirrors tests/test_responses_read_api.py).

Surveys are created via the ORM with status=published and a minimal valid question set (the
brief permits ORM creation; this bypasses the authenticated write-validator, which is a
separate wave's surface).
"""
import uuid

import pytest
from django.core.cache import cache
from django.db import connection
from rest_framework.test import APIClient

from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.responses.models import Display, Response, SurveyLink
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

SLUG = "runtime-org"

# A minimal but schema-shaped question set. `q1` is required; `q2` optional. Used for the
# answer-key + required-gate tests.
QUESTIONS = [
    {
        "id": "q1",
        "type": "openText",
        "headline": {"default": "Your name?"},
        "required": True,
        "config": {"inputType": "short"},
    },
    {
        "id": "q2",
        "type": "nps",
        "headline": {"default": "How likely to recommend?"},
        "required": False,
        "config": {},
    },
]
LANGUAGES = [{"code": "default", "label": "Default", "default": True}]


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """Reset the throttle cache between tests so per-IP buckets don't bleed across cases."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def published_ctx(transactional_db):
    """Org + workspace + env + a PUBLISHED survey. Yields (org, env, survey).

    An owner User is needed only to create the org (create_organization requires it); the
    public path itself is anonymous.
    """
    connection.set_schema_to_public()
    from django.contrib.auth import get_user_model

    User = get_user_model()
    User.objects.filter(email="runtime_owner@example.com").delete()
    owner = User.objects.create_user(
        email="runtime_owner@example.com", password="x", is_verified=True
    )
    _cleanup(SLUG)
    org = create_organization(name="Runtime Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = Survey.objects.create(
        environment=env,
        name="Customer Survey",
        status=SurveyStatus.PUBLISHED,
        questions=QUESTIONS,
        languages=LANGUAGES,
        welcome_card={"enabled": True, "headline": {"default": "Welcome"}},
        endings=[{"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}}],
    )
    yield org, env, survey
    _cleanup(SLUG)


def _mk_survey(org, env, **kwargs):
    connection.set_tenant(org)
    defaults = dict(
        name="S", status=SurveyStatus.PUBLISHED, questions=QUESTIONS, languages=LANGUAGES
    )
    defaults.update(kwargs)
    return Survey.objects.create(environment=env, **defaults)


def _mk_link(org, survey, **kwargs):
    connection.set_tenant(org)
    token = kwargs.pop("token", uuid.uuid4().hex)
    return SurveyLink.objects.create(survey=survey, token=token, **kwargs)


def _create(api, survey, body=None):
    return api.post(
        f"/api/v1/{SLUG}/public/surveys/{survey.id}/responses",
        body or {},
        format="json",
    )


# --------------------------------------------------------------------------- #
#  GET public survey definition                                               #
# --------------------------------------------------------------------------- #
def test_get_published_survey_returns_public_shape(api, published_ctx):
    org, env, survey = published_ctx
    resp = api.get(f"/api/v1/{SLUG}/public/surveys/{survey.id}")
    assert resp.status_code == 200
    body = resp.json()
    # Strict public allow-list (openapi PublicSurvey). `blocks` is exposed so the runtime can
    # apply within-block presentation randomization; `variables` so the runtime can seed +
    # thread variable state for recall + conditions (both additive; [] == prior behavior).
    assert set(body) == {
        "id", "name", "questions", "languages", "styling", "welcomeCard",
        "endings", "hiddenFields", "blocks", "variables", "single_use",
    }
    assert body["id"] == str(survey.id)
    assert body["name"] == "Customer Survey"
    assert len(body["questions"]) == 2
    # welcomeCard is camelCase in the PUBLIC shape (openapi), sourced from welcome_card.
    assert body["welcomeCard"]["enabled"] is True
    assert body["single_use"] is False


def test_get_published_survey_no_admin_field_leak(api, published_ctx):
    """The public shape must NOT leak environment_id/status/created_by/timestamps/close_on/logic."""
    org, env, survey = published_ctx
    body = api.get(f"/api/v1/{SLUG}/public/surveys/{survey.id}").json()
    for leaked in (
        "environment_id", "status", "created_by_user_id", "created_at", "updated_at",
        "published_at", "close_on", "logic", "welcome_card",  # snake_card name must not appear
    ):
        assert leaked not in body, f"leaked admin/internal field: {leaked}"


def test_get_unpublished_survey_404(api, published_ctx):
    """draft/paused/closed must 404 (no leak, no status oracle)."""
    org, env, survey = published_ctx
    for st in (SurveyStatus.DRAFT, SurveyStatus.PAUSED, SurveyStatus.CLOSED):
        s = _mk_survey(org, env, status=st)
        resp = api.get(f"/api/v1/{SLUG}/public/surveys/{s.id}")
        assert resp.status_code == 404, f"status={st} should 404"


def test_get_unknown_survey_404(api, published_ctx):
    org, env, survey = published_ctx
    resp = api.get(f"/api/v1/{SLUG}/public/surveys/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_survey_consumed_link_410(api, published_ctx):
    """A single-use survey accessed via an already-consumed link → 410."""
    org, env, survey = published_ctx
    survey.single_use = True
    connection.set_tenant(org)
    survey.save(update_fields=["single_use"])
    from django.utils import timezone

    link = _mk_link(org, survey, consumed_at=timezone.now())
    resp = api.get(f"/api/v1/{SLUG}/public/surveys/{survey.id}?link_token={link.token}")
    assert resp.status_code == 410


def test_get_single_use_survey_without_link_410(api, published_ctx):
    """A single_use survey with NO link_token is not accessible → 410 (needs a link)."""
    org, env, survey = published_ctx
    s = _mk_survey(org, env, single_use=True)
    resp = api.get(f"/api/v1/{SLUG}/public/surveys/{s.id}")
    assert resp.status_code == 410


# --------------------------------------------------------------------------- #
#  create → patch → finalize via edit_token                                   #
# --------------------------------------------------------------------------- #
def test_create_response_returns_handle_with_edit_token(api, published_ctx):
    org, env, survey = published_ctx
    resp = _create(api, survey, {"data": {"q1": "Ada"}, "language": "en"})
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "finished", "edit_token", "expires_at"}
    assert body["finished"] is False
    assert body["edit_token"]
    # The response row exists with the initial answer + server-derived meta (anonymous).
    connection.set_tenant(org)
    r = Response.objects.get(pk=body["id"])
    assert r.data == {"q1": "Ada"}
    assert r.language == "en"
    assert r.contact_id is None  # anonymous (no link)


def test_create_then_patch_then_finalize_happy_path(api, published_ctx):
    org, env, survey = published_ctx
    # Create empty, then patch q1 (required), then finalize.
    created = _create(api, survey, {"data": {}}).json()
    rid, token = created["id"], created["edit_token"]

    patch = api.patch(
        f"/api/v1/{SLUG}/public/responses/{rid}?edit_token={token}",
        {"data": {"q1": "Grace"}},
        format="json",
    )
    assert patch.status_code == 200
    assert patch.json()["finished"] is False

    fin = api.post(f"/api/v1/{SLUG}/public/responses/{rid}/finalize?edit_token={token}")
    assert fin.status_code == 200
    assert fin.json()["finished"] is True

    connection.set_tenant(org)
    r = Response.objects.get(pk=rid)
    assert r.finished is True
    assert r.finished_at is not None
    assert r.data == {"q1": "Grace"}


def test_finalize_returns_over_quota_when_full(api, published_ctx):
    """End-to-end: a quota limit-1 survey marks the 2nd matching finalize over-quota; the finalize
    body carries over_quota + quota_full_ending_id. The 1st (within limit) returns the byte-
    identical pre-quota handle (no quota keys)."""
    org, env, _survey = published_ctx
    connection.set_tenant(org)
    # q1 is required; a quota on q1 == "Ada", limit 1, routing over-quota to a "quota_full" ending.
    survey = _mk_survey(
        org,
        env,
        name="Quota survey",
        endings=[
            {"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}},
            {"id": "quota_full", "type": "endScreen", "headline": {"default": "Full"}},
        ],
        quotas=[
            {
                "id": "q_ada",
                "name": "Adas",
                "limit": 1,
                "condition": {"leftQuestionId": "q1", "operator": "equals", "value": "Ada"},
                "endingId": "quota_full",
            }
        ],
    )

    def _create_finalize(name):
        created = _create(api, survey, {"data": {"q1": name}}).json()
        rid, token = created["id"], created["edit_token"]
        return api.post(f"/api/v1/{SLUG}/public/responses/{rid}/finalize?edit_token={token}")

    # 1st Ada fills the limit-1 quota -> within limit -> byte-identical pre-quota handle.
    first = _create_finalize("Ada")
    assert first.status_code == 200
    assert set(first.json()) == {"id", "finished", "edit_token", "expires_at"}

    # 2nd Ada is the (limit+1)th match -> over-quota, routed to the quota's ending.
    second = _create_finalize("Ada")
    assert second.status_code == 200
    body = second.json()
    assert body["finished"] is True
    assert body["over_quota"] is True
    assert body["quota_full_ending_id"] == "quota_full"

    # A non-matching respondent (q1 != "Ada") is unaffected even though the quota is full.
    other = _create_finalize("Grace")
    assert set(other.json()) == {"id", "finished", "edit_token", "expires_at"}


def test_patch_merges_idempotent_per_key(api, published_ctx):
    org, env, survey = published_ctx
    created = _create(api, survey, {"data": {"q1": "A"}}).json()
    rid, token = created["id"], created["edit_token"]
    # Patch q2; q1 must survive (merge, not replace). Re-patch q1 overwrites (idempotent key).
    api.patch(f"/api/v1/{SLUG}/public/responses/{rid}?edit_token={token}",
              {"data": {"q2": 9}}, format="json")
    api.patch(f"/api/v1/{SLUG}/public/responses/{rid}?edit_token={token}",
              {"data": {"q1": "B"}}, format="json")
    connection.set_tenant(org)
    r = Response.objects.get(pk=rid)
    assert r.data == {"q1": "B", "q2": 9}


def test_patch_with_bad_edit_token_403(api, published_ctx):
    org, env, survey = published_ctx
    created = _create(api, survey, {"data": {}}).json()
    rid = created["id"]
    resp = api.patch(
        f"/api/v1/{SLUG}/public/responses/{rid}?edit_token=not-a-real-token",
        {"data": {"q1": "x"}},
        format="json",
    )
    assert resp.status_code == 403


def test_edit_token_is_single_response_scoped(api, published_ctx):
    """A token minted for response A must not authorize editing response B (REVIEW.md §3.5)."""
    org, env, survey = published_ctx
    a = _create(api, survey, {"data": {}}).json()
    b = _create(api, survey, {"data": {}}).json()
    # Use A's token against B's id → 403.
    resp = api.patch(
        f"/api/v1/{SLUG}/public/responses/{b['id']}?edit_token={a['edit_token']}",
        {"data": {"q1": "x"}},
        format="json",
    )
    assert resp.status_code == 403


def test_finalize_missing_required_400(api, published_ctx):
    """Finalize with the required q1 unanswered → 400 (required gate via logic evaluator)."""
    org, env, survey = published_ctx
    created = _create(api, survey, {"data": {"q2": 7}}).json()  # only the optional q2
    rid, token = created["id"], created["edit_token"]
    fin = api.post(f"/api/v1/{SLUG}/public/responses/{rid}/finalize?edit_token={token}")
    assert fin.status_code == 400
    assert "q1" in fin.json()["errors"]["required"]


def test_finalize_with_bad_token_403(api, published_ctx):
    org, env, survey = published_ctx
    created = _create(api, survey, {"data": {"q1": "x"}}).json()
    rid = created["id"]
    fin = api.post(f"/api/v1/{SLUG}/public/responses/{rid}/finalize?edit_token=bogus")
    assert fin.status_code == 403


# --------------------------------------------------------------------------- #
#  Answer validation against the survey's question set                        #
# --------------------------------------------------------------------------- #
def test_create_unknown_question_key_400(api, published_ctx):
    org, env, survey = published_ctx
    resp = _create(api, survey, {"data": {"nope": "x", "q1": "ok"}})
    assert resp.status_code == 400
    assert "nope" in resp.json()["errors"]["data"]


def test_patch_unknown_question_key_400(api, published_ctx):
    org, env, survey = published_ctx
    created = _create(api, survey, {"data": {}}).json()
    rid, token = created["id"], created["edit_token"]
    resp = api.patch(
        f"/api/v1/{SLUG}/public/responses/{rid}?edit_token={token}",
        {"data": {"ghost": 1}},
        format="json",
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
#  Survey-level hidden fields (captured into Response.data from the URL)       #
# --------------------------------------------------------------------------- #
def test_public_survey_exposes_hidden_fields(api, published_ctx):
    """The PublicSurvey shape carries hiddenFields (camelCase) so the runtime can
    capture matching ?param= values."""
    org, env, survey = published_ctx
    s = _mk_survey(
        org, env, hidden_fields=[{"id": "userId", "name": "User ID"}, {"id": "plan"}]
    )
    body = api.get(f"/api/v1/{SLUG}/public/surveys/{s.id}").json()
    assert body["hiddenFields"] == [{"id": "userId", "name": "User ID"}, {"id": "plan"}]


def test_create_response_accepts_hidden_field_value(api, published_ctx):
    """A declared hidden field id is an ACCEPTED Response.data key (the runtime captures
    it from the URL). An UNDECLARED key is still rejected."""
    org, env, survey = published_ctx
    s = _mk_survey(org, env, hidden_fields=[{"id": "userId"}])
    # Declared hidden field + a real question id → accepted.
    ok = _create(api, s, {"data": {"q1": "Ada", "userId": "123"}})
    assert ok.status_code == 201
    rid = ok.json()["id"]
    connection.set_tenant(org)
    stored = Response.objects.get(pk=rid)
    assert stored.data["userId"] == "123"
    # An undeclared key is still rejected.
    bad = _create(api, s, {"data": {"q1": "Ada", "ghost": "x"}})
    assert bad.status_code == 400
    assert "ghost" in bad.json()["errors"]["data"]


# --------------------------------------------------------------------------- #
#  single-use link consumption + contact derivation                           #
# --------------------------------------------------------------------------- #
def test_single_use_link_consumed_on_finalize(api, published_ctx):
    org, env, survey = published_ctx
    s = _mk_survey(org, env, single_use=True)
    link = _mk_link(org, s)
    created = _create(api, s, {"data": {"q1": "Ada"}, "link_token": link.token})
    assert created.status_code == 201
    rid, token = created.json()["id"], created.json()["edit_token"]

    # Not consumed yet (only on finalize).
    connection.set_tenant(org)
    assert SurveyLink.objects.get(pk=link.id).consumed_at is None

    fin = api.post(f"/api/v1/{SLUG}/public/responses/{rid}/finalize?edit_token={token}")
    assert fin.status_code == 200
    connection.set_tenant(org)
    consumed = SurveyLink.objects.get(pk=link.id)
    assert consumed.consumed_at is not None
    assert consumed.response_id == uuid.UUID(rid)


def test_single_use_link_reuse_blocked_410(api, published_ctx):
    """After a single-use link is consumed, a NEW create with it → 410."""
    org, env, survey = published_ctx
    s = _mk_survey(org, env, single_use=True)
    link = _mk_link(org, s)
    first = _create(api, s, {"data": {"q1": "Ada"}, "link_token": link.token}).json()
    api.post(f"/api/v1/{SLUG}/public/responses/{first['id']}/finalize?edit_token={first['edit_token']}")
    # Second attempt with the same (now consumed) link.
    second = _create(api, s, {"data": {"q1": "Bob"}, "link_token": link.token})
    assert second.status_code == 410


def test_single_use_survey_requires_link_on_create_410(api, published_ctx):
    org, env, survey = published_ctx
    s = _mk_survey(org, env, single_use=True)
    resp = _create(api, s, {"data": {"q1": "Ada"}})  # no link_token
    assert resp.status_code == 410


def test_contact_derived_from_link_not_client(api, published_ctx):
    """Response.contact_id comes from SurveyLink.contact_id, NEVER a client-supplied id."""
    org, env, survey = published_ctx
    link_contact = uuid.uuid4()
    attacker_contact = uuid.uuid4()
    link = _mk_link(org, survey, contact_id=link_contact)
    # Client tries to inject a different contact_id; it must be ignored (unknown field).
    created = _create(
        api,
        survey,
        {"data": {"q1": "Ada"}, "link_token": link.token, "contact_id": str(attacker_contact)},
    )
    assert created.status_code == 201
    connection.set_tenant(org)
    r = Response.objects.get(pk=created.json()["id"])
    assert r.contact_id == link_contact      # from the link
    assert r.contact_id != attacker_contact  # NOT from the client


def test_expired_link_410(api, published_ctx):
    org, env, survey = published_ctx
    from datetime import timedelta

    from django.utils import timezone

    s = _mk_survey(org, env, single_use=True)
    link = _mk_link(org, s, expires_at=timezone.now() - timedelta(hours=1))
    resp = _create(api, s, {"data": {"q1": "x"}, "link_token": link.token})
    assert resp.status_code == 410


# --------------------------------------------------------------------------- #
#  close_on auto-close (openapi POST-create 410 "survey closed (cap/date)")    #
# --------------------------------------------------------------------------- #
def test_create_blocked_by_close_on_date_410(api, published_ctx):
    """A published survey past its close_on.by_date refuses NEW responses → 410."""
    org, env, survey = published_ctx
    from datetime import timedelta

    from django.utils import timezone

    past = (timezone.now() - timedelta(hours=1)).isoformat()
    s = _mk_survey(org, env, close_on={"by_date": past})
    resp = _create(api, s, {"data": {"q1": "x"}})
    assert resp.status_code == 410
    assert resp.json()["code"] == "survey_closed"


def test_create_blocked_by_response_cap_410(api, published_ctx):
    """Once finished responses reach by_response_cap, new creates are refused → 410."""
    org, env, survey = published_ctx
    s = _mk_survey(org, env, close_on={"by_response_cap": 1})
    # One FINISHED response reaches the cap of 1.
    connection.set_tenant(org)
    Response.objects.create(survey=s, data={"q1": "done"}, finished=True)
    resp = _create(api, s, {"data": {"q1": "x"}})
    assert resp.status_code == 410
    assert resp.json()["code"] == "survey_closed"


def test_create_allowed_under_response_cap(api, published_ctx):
    """Below the cap (unfinished responses don't count), create still succeeds."""
    org, env, survey = published_ctx
    s = _mk_survey(org, env, close_on={"by_response_cap": 2})
    connection.set_tenant(org)
    # An unfinished (partial) response does NOT count toward the cap.
    Response.objects.create(survey=s, data={}, finished=False)
    resp = _create(api, s, {"data": {"q1": "x"}})
    assert resp.status_code == 201


def test_get_definition_still_served_when_closed(api, published_ctx):
    """An auto-closed survey still serves its GET definition (runtime shows a closed message)."""
    org, env, survey = published_ctx
    from datetime import timedelta

    from django.utils import timezone

    past = (timezone.now() - timedelta(hours=1)).isoformat()
    s = _mk_survey(org, env, close_on={"by_date": past})
    assert api.get(f"/api/v1/{SLUG}/public/surveys/{s.id}").status_code == 200


# --------------------------------------------------------------------------- #
#  Display tracking                                                           #
# --------------------------------------------------------------------------- #
def test_display_recorded_anonymous(api, published_ctx):
    org, env, survey = published_ctx
    resp = api.post(f"/api/v1/{SLUG}/public/surveys/{survey.id}/displays", {}, format="json")
    assert resp.status_code == 201
    assert "display_id" in resp.json()
    connection.set_tenant(org)
    d = Display.objects.get(pk=resp.json()["display_id"])
    assert d.survey_id == survey.id
    assert d.contact_id is None


def test_display_associates_contact_from_link(api, published_ctx):
    org, env, survey = published_ctx
    contact = uuid.uuid4()
    link = _mk_link(org, survey, contact_id=contact)
    resp = api.post(
        f"/api/v1/{SLUG}/public/surveys/{survey.id}/displays",
        {"link_token": link.token},
        format="json",
    )
    assert resp.status_code == 201
    connection.set_tenant(org)
    d = Display.objects.get(pk=resp.json()["display_id"])
    assert d.contact_id == contact


def test_display_unpublished_survey_404(api, published_ctx):
    org, env, survey = published_ctx
    s = _mk_survey(org, env, status=SurveyStatus.DRAFT)
    resp = api.post(f"/api/v1/{SLUG}/public/surveys/{s.id}/displays", {}, format="json")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
#  no-auth-leak / anonymity                                                   #
# --------------------------------------------------------------------------- #
def test_public_endpoints_need_no_auth(api, published_ctx):
    """The runtime path is fully anonymous: no 401/403 for missing auth on the public ops."""
    org, env, survey = published_ctx
    # GET definition + create + display all succeed WITHOUT any auth.
    assert api.get(f"/api/v1/{SLUG}/public/surveys/{survey.id}").status_code == 200
    assert _create(api, survey, {"data": {"q1": "x"}}).status_code == 201
    assert api.post(
        f"/api/v1/{SLUG}/public/surveys/{survey.id}/displays", {}, format="json"
    ).status_code == 201


def test_public_get_unknown_org_404(api, published_ctx):
    """An unknown org slug 404s at the middleware (no tenant-existence oracle)."""
    org, env, survey = published_ctx
    resp = api.get(f"/api/v1/no-such-org/public/surveys/{survey.id}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
#  rate limiting                                                              #
# --------------------------------------------------------------------------- #
def test_create_rate_limited_429(api, published_ctx):
    """The strict create throttle (20/min/IP/survey) returns 429 once the bucket is exhausted."""
    org, env, survey = published_ctx
    last = None
    # 20 allowed, the 21st should be 429.
    for _ in range(21):
        last = _create(api, survey, {"data": {"q1": "x"}})
    assert last.status_code == 429
    # DRF's Throttled sets Retry-After from the throttle's wait() (openapi RateLimited header).
    assert last.headers.get("Retry-After") is not None


def test_rate_limit_is_per_survey(api, published_ctx):
    """Exhausting survey A's create bucket must NOT block survey B (per-IP+per-survey scope)."""
    org, env, survey = published_ctx
    other = _mk_survey(org, env, name="Other")
    # Exhaust A.
    for _ in range(21):
        _create(api, survey, {"data": {"q1": "x"}})
    # B still has its own bucket.
    resp = _create(api, other, {"data": {"q1": "x"}})
    assert resp.status_code == 201


# --------------------------------------------------------------------------- #
#  HTML host page + deferred upload-url                                       #
# --------------------------------------------------------------------------- #
def test_html_host_page_renders_and_mounts_island(api, published_ctx):
    org, env, survey = published_ctx
    resp = api.get(f"/{SLUG}/s/{survey.id}")
    assert resp.status_code == 200
    html = resp.content.decode()
    # The runtime mount point + injected survey id + the json_script config tag.
    assert 'id="opensurvey-runtime"' in html
    assert f'data-survey-id="{survey.id}"' in html
    # Mount contract the runtime island (main.tsx) reads: org slug + api base as dataset attrs.
    assert f'data-org-slug="{SLUG}"' in html
    assert 'data-api-base="/api/v1"' in html  # ROOT; the island appends /{orgSlug}/public/... itself
    assert 'id="opensurvey-runtime-config"' in html
    # The injected JSON config carries the survey id (json_script serializes the dict).
    assert str(survey.id) in html


def test_html_host_page_unpublished_404(api, published_ctx):
    org, env, survey = published_ctx
    s = _mk_survey(org, env, status=SurveyStatus.DRAFT)
    assert api.get(f"/{SLUG}/s/{s.id}").status_code == 404


def test_html_host_page_unknown_survey_404(api, published_ctx):
    org, env, survey = published_ctx
    assert api.get(f"/{SLUG}/s/{uuid.uuid4()}").status_code == 404


def test_upload_url_is_deferred_not_routed(api, published_ctx):
    """The presigned upload-url endpoint is DEFERRED — it must not be routed (404, not 405)."""
    org, env, survey = published_ctx
    resp = api.post(
        f"/api/v1/{SLUG}/public/surveys/{survey.id}/upload-url",
        {"question_id": "q1", "filename": "f.png", "content_type": "image/png", "size_bytes": 1},
        format="json",
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
#  capture_ip gate (Settings tab; off by default — GDPR-friendly)             #
# --------------------------------------------------------------------------- #
def test_capture_ip_off_by_default_drops_ip(api, published_ctx):
    """Default survey (capture_ip=False) must NOT store meta.ip — even if the client
    tries to supply one (the client can't force PII capture)."""
    org, env, survey = published_ctx  # capture_ip defaults False
    resp = api.post(
        f"/api/v1/{SLUG}/public/surveys/{survey.id}/responses",
        {"data": {"q1": "Ada"}, "meta": {"url": "https://x/y", "ip": "1.2.3.4"}},
        format="json",
        REMOTE_ADDR="203.0.113.9",
    )
    assert resp.status_code == 201
    connection.set_tenant(org)
    r = Response.objects.get(pk=resp.json()["id"])
    assert "ip" not in r.meta  # not captured (privacy default)
    assert r.meta.get("url") == "https://x/y"  # non-ip client meta preserved


def test_capture_ip_on_stores_server_ip(api, published_ctx):
    """capture_ip=True stores the SERVER-derived ip (REMOTE_ADDR), never the
    client-supplied value."""
    org, env, survey = published_ctx
    connection.set_tenant(org)
    survey.capture_ip = True
    survey.save(update_fields=["capture_ip"])

    resp = api.post(
        f"/api/v1/{SLUG}/public/surveys/{survey.id}/responses",
        {"data": {"q1": "Ada"}, "meta": {"ip": "1.2.3.4"}},  # client ip is ignored
        format="json",
        REMOTE_ADDR="203.0.113.9",
    )
    assert resp.status_code == 201
    connection.set_tenant(org)
    r = Response.objects.get(pk=resp.json()["id"])
    assert r.meta.get("ip") == "203.0.113.9"  # server REMOTE_ADDR, not client "1.2.3.4"
