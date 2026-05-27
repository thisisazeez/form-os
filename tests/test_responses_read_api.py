"""
HTTP-level tests for the Response READ API (contracts/openapi.yaml — Responses tag, read side).

Wave 2c serves ONLY the authenticated read path: list responses for a survey + get a single
response. The public submission path and the authenticated write actions (delete/tag/export) are
out of scope and are NOT routed — verified here too.

Responses are created via the ORM (the brief permits this: "Tests may create Response rows via
the ORM"). Real request path through TenantSlugMiddleware; auth via DRF ``force_authenticate``;
``transactional_db`` + ``force_drop`` for the schema-provisioning DDL.

Covers: list scoped to a survey (read gate, 404-on-invisible-survey, finished + created_*
filters, pagination envelope), detail get (404-on-invisible), the ``meta.ip`` PII redaction
(rbac.md §11), tags inclusion, the ``segment_id`` 400 (analytics-wave), and that write routes
are absent (405/404).
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.responses.models import Response, ResponseTag, Tag
from apps.surveys.models import Survey
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "resp-api-org"


@pytest.fixture
def api():
    return APIClient()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def survey_ctx(transactional_db):
    """Org + owner + workspace + env + a survey. Yields (org, owner, env, survey)."""
    owner = _mk_user("resp_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Resp API Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = Survey.objects.create(environment=env, name="S", questions=[])
    yield org, owner, env, survey
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


def _grant(org, user, workspace_id, access):
    connection.set_tenant(org)
    team = Team.objects.create(name=f"team-{user.email}-{access}")
    TeamMembership.objects.create(team=team, user_id=user.id)
    WorkspaceTeam.objects.create(team=team, workspace_id=workspace_id, access=access)


def _mk_response(org, survey, **kwargs):
    connection.set_tenant(org)
    return Response.objects.create(survey=survey, **kwargs)


# --------------------------------------------------------------------------- #
#  LIST  (GET /surveys/{id}/responses)                                         #
# --------------------------------------------------------------------------- #
def test_list_responses_owner(api, survey_ctx):
    org, owner, env, survey = survey_ctx
    _mk_response(org, survey, data={"nps": 9}, finished=True)
    _mk_response(org, survey, data={"nps": 3}, finished=False)
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{SLUG}/surveys/{survey.id}/responses")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"count", "page", "page_size", "results"}
    assert body["count"] == 2
    assert all("data" in r and "tags" in r for r in body["results"])


def test_list_responses_reader_allowed(api, survey_ctx):
    org, owner, env, survey = survey_ctx
    _mk_response(org, survey, data={"a": 1})
    reader = _add_member(org, "resp_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.get(f"/api/v1/{SLUG}/surveys/{survey.id}/responses")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_list_responses_invisible_survey_404(api, survey_ctx):
    org, owner, env, survey = survey_ctx
    _mk_response(org, survey, data={"a": 1})
    member = _add_member(org, "resp_list_noacc@example.com")
    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/surveys/{survey.id}/responses")
    # No read access to the survey's workspace → 404 (no existence oracle).
    assert resp.status_code == 404


def test_list_responses_finished_filter(api, survey_ctx):
    org, owner, env, survey = survey_ctx
    _mk_response(org, survey, data={"a": 1}, finished=True)
    _mk_response(org, survey, data={"a": 2}, finished=False)
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{SLUG}/surveys/{survey.id}/responses?finished=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["results"][0]["finished"] is True


def test_list_responses_created_range_filter(api, survey_ctx):
    from datetime import timedelta

    from django.utils import timezone

    org, owner, env, survey = survey_ctx
    r_old = _mk_response(org, survey, data={"a": 1})
    r_new = _mk_response(org, survey, data={"a": 2})
    # Force distinct created_at so the range filter is meaningful.
    connection.set_tenant(org)
    old_time = timezone.now() - timedelta(days=2)
    Response.objects.filter(pk=r_old.pk).update(created_at=old_time)

    api.force_authenticate(user=owner)
    cutoff = (timezone.now() - timedelta(days=1)).isoformat()
    # Pass via the params dict so DRF/the test client URL-encodes the '+' offset correctly
    # (an unencoded '+' would arrive as a space; the view tolerates that too — see _parse_dt).
    resp = api.get(
        f"/api/v1/{SLUG}/surveys/{survey.id}/responses", {"created_after": cutoff}
    )
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["results"]}
    assert str(r_new.id) in ids
    assert str(r_old.id) not in ids


def test_list_responses_segment_id_unsupported_400(api, survey_ctx):
    """segment_id evaluation is analytics-wave; we reject with a clear 400 (no silent mis-filter)."""
    org, owner, env, survey = survey_ctx
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{SLUG}/surveys/{survey.id}/responses?segment_id={uuid.uuid4()}")
    assert resp.status_code == 400
    assert resp.json()["code"] == "segment_filter_unsupported"


def test_list_responses_meta_ip_redacted(api, survey_ctx):
    """meta.ip is PII (rbac.md §11) and must not be echoed back through the read API."""
    org, owner, env, survey = survey_ctx
    _mk_response(
        org,
        survey,
        data={"a": 1},
        meta={"url": "https://x/y", "source": "link", "ip": "203.0.113.7", "deviceType": "desktop"},
    )
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{SLUG}/surveys/{survey.id}/responses")
    assert resp.status_code == 200
    meta = resp.json()["results"][0]["meta"]
    assert "ip" not in meta
    assert meta["url"] == "https://x/y"  # non-PII fields preserved


# --------------------------------------------------------------------------- #
#  DETAIL  (GET /responses/{id})                                               #
# --------------------------------------------------------------------------- #
def test_get_response_owner(api, survey_ctx):
    org, owner, env, survey = survey_ctx
    r = _mk_response(org, survey, data={"nps": 8}, language="en", finished=True)
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{SLUG}/responses/{r.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(r.id)
    assert body["survey_id"] == str(survey.id)
    assert body["data"] == {"nps": 8}
    assert body["language"] == "en"


def test_get_response_invisible_404(api, survey_ctx):
    org, owner, env, survey = survey_ctx
    r = _mk_response(org, survey, data={"a": 1})
    member = _add_member(org, "resp_get_noacc@example.com")
    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/responses/{r.id}")
    assert resp.status_code == 404


def test_get_response_with_tags(api, survey_ctx):
    org, owner, env, survey = survey_ctx
    r = _mk_response(org, survey, data={"a": 1})
    connection.set_tenant(org)
    tag = Tag.objects.create(name="VIP", color="#ff0000")
    ResponseTag.objects.create(response=r, tag=tag)
    api.force_authenticate(user=owner)
    resp = api.get(f"/api/v1/{SLUG}/responses/{r.id}")
    assert resp.status_code == 200
    tags = resp.json()["tags"]
    assert len(tags) == 1
    assert tags[0]["name"] == "VIP"
    assert tags[0]["color"] == "#ff0000"


def test_response_read_endpoints_require_auth(api, survey_ctx):
    org, owner, env, survey = survey_ctx
    assert api.get(f"/api/v1/{SLUG}/surveys/{survey.id}/responses").status_code in (401, 403)
    r = _mk_response(org, survey, data={"a": 1})
    assert api.get(f"/api/v1/{SLUG}/responses/{r.id}").status_code in (401, 403)


# --------------------------------------------------------------------------- #
#  WRITE paths are OUT OF SCOPE for this wave — verify they are not served.    #
# --------------------------------------------------------------------------- #
def test_response_write_paths_not_implemented(api, survey_ctx):
    """DELETE response / POST tags / POST export are authenticated WRITES — not this wave.

    The read URLconf does not route them; DELETE on the detail route is method-not-allowed (405)
    and the tag/export paths are unrouted (404). This pins the scope boundary.
    """
    org, owner, env, survey = survey_ctx
    r = _mk_response(org, survey, data={"a": 1})
    api.force_authenticate(user=owner)
    # DELETE on /responses/{id} — the view defines only GET → 405.
    assert api.delete(f"/api/v1/{SLUG}/responses/{r.id}").status_code == 405
    # POST /responses/{id}/tags — unrouted → 404.
    assert api.post(f"/api/v1/{SLUG}/responses/{r.id}/tags", {"name": "x"}, format="json").status_code == 404
    # POST /surveys/{id}/responses/export — unrouted → 404.
    assert api.post(f"/api/v1/{SLUG}/surveys/{survey.id}/responses/export", {}, format="json").status_code == 404
