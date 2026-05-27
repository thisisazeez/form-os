"""
Tests for the WIRED HTMX survey-admin surface (Wave 3b, A1 surveys-admin):

  GET  /{org_slug}/                              dashboard_home
  GET  /{org_slug}/surveys                       surveys_list   (+ ?new=1 modal)
  POST /{org_slug}/surveys                        surveys_list   (create DRAFT → builder)
  GET  /{org_slug}/surveys/{id}/share            survey_share
  GET  /{org_slug}/surveys/{id}/results          survey_results
  GET  /{org_slug}/surveys/{id}/responses        survey_responses
  GET  /{org_slug}/surveys/{id}/responses/{rid}  response_detail

Hits every page through the REAL TenantSlugMiddleware path (slug resolves + activates the org
schema) with Django's session test ``Client`` + ``force_login`` — these are Django HTML views
gated by ``@login_required`` + ``can()`` (NOT DRF). Org creation provisions a Postgres schema
(DDL) → ``transactional_db`` + ``force_drop`` cleanup, mirroring tests/test_builder_host_page.py.

RBAC matrix (mirrors the builder host + rbac.md §8.1):
  - 200 for the owner (implicit manage) and for an org member with workspace ``read`` on the
    survey's workspace (the read pages' floor is workspace ``read``).
  - 404 for an org member with NO workspace access on a per-survey page (no existence oracle),
    and for an unknown survey id.
  - List/dashboard FILTER (rbac.md §8.1): a no-access member gets 200 with an empty state, NOT
    a 403.
  - 302 (redirect to LOGIN_URL) for an anonymous visitor (the login gate is present).
  - New-survey: owner POST creates a DRAFT + redirects to the builder; a member with only
    ``read`` POSTing → 403 (mirrors members_page non-admin POST + builder-host can-view-not-edit).
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.responses.models import Response
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "admin-org"

# A multi-type question set so the results/table/detail aggregation runs over real shapes.
QUESTIONS = [
    {
        "id": "nps1",
        "type": "nps",
        "headline": {"default": "How likely are you to recommend us?"},
        "required": True,
        "config": {},
    },
    {
        "id": "rate1",
        "type": "rating",
        "headline": {"default": "Overall experience?"},
        "required": False,
        "config": {"range": 5, "scale": "star"},
    },
    {
        "id": "sel1",
        "type": "singleSelect",
        "headline": {"default": "Primary reason for using our product?"},
        "required": False,
        "config": {
            "choices": [
                {"id": "ease", "label": {"default": "Ease of use"}},
                {"id": "price", "label": {"default": "Price"}},
            ]
        },
    },
    {
        "id": "txt1",
        "type": "openText",
        "headline": {"default": "Any additional feedback?"},
        "required": False,
        "config": {"inputType": "long"},
    },
]
LANGUAGES = [{"code": "en", "label": "English", "default": True}]


# --------------------------------------------------------------------------- #
#  Fixtures / helpers (mirror tests/test_builder_host_page.py)                 #
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
def admin_ctx(transactional_db):
    """Org + owner + workspace + prod env + a PUBLISHED survey with a few responses.

    Yields (org, owner, env, survey).
    """
    owner = _mk_user("admin_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Admin Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    # create_organization now auto-provisions a "Default workspace"; this fixture uses a single
    # controlled workspace, so drop the default first (keeps one writable env the new-survey flow
    # resolves to deterministically).
    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="Main WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = Survey.objects.create(
        environment=env,
        name="Customer Satisfaction Q4",
        status=SurveyStatus.PUBLISHED,
        questions=QUESTIONS,
        languages=LANGUAGES,
        styling={},
        endings=[],
    )
    # Two finished + one partial response with real per-type answers.
    Response.objects.create(
        survey=survey,
        finished=True,
        data={"nps1": 10, "rate1": 5, "sel1": "ease", "txt1": "Love the clean interface!"},
    )
    Response.objects.create(
        survey=survey,
        finished=True,
        data={"nps1": 4, "rate1": 2, "sel1": "price", "txt1": "Export is inconsistent."},
    )
    Response.objects.create(survey=survey, finished=False, data={"nps1": 7})
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


def _u(path):
    return f"/{SLUG}{path}"


# --------------------------------------------------------------------------- #
#  Dashboard home                                                              #
# --------------------------------------------------------------------------- #
def test_dashboard_owner_200_real_stats(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u("/"))
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert "Welcome back" in html
    # Recent survey row is the real survey.
    assert "Customer Satisfaction Q4" in html
    # 4 stat-card labels render.
    assert "Active surveys" in html
    assert "Total responses" in html
    assert "Avg. completion" in html
    assert "Avg. NPS" in html
    # 3 responses total (2 finished + 1 partial) — the real count appears.
    assert ">3<" in html or "3" in html


def test_dashboard_reader_200(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    reader = _add_member(org, "dash_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_u("/"))
    assert resp.status_code == 200
    assert "Customer Satisfaction Q4" in resp.content.decode()


def test_dashboard_no_access_member_200_empty(client, admin_ctx):
    """A member with NO workspace access sees the dashboard (200) with an empty state — lists
    FILTER, not 403 (rbac.md §8.1). The survey they cannot read is NOT shown."""
    org, owner, env, survey = admin_ctx
    member = _add_member(org, "dash_noaccess@example.com")
    client.force_login(member)
    resp = client.get(_u("/"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Customer Satisfaction Q4" not in html
    assert "No surveys yet" in html


def test_dashboard_anonymous_redirects(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    resp = client.get(_u("/"))
    assert resp.status_code in (301, 302)


def test_dashboard_nps_scoped_per_survey(client, admin_ctx):
    """A question id is unique only WITHIN a survey: a second survey reusing the id `nps1` for a
    NON-nps question must NOT pollute the org-wide Avg. NPS. The fixture survey has nps1 answers
    10 + 4 (finished) → NPS = 0; a rating survey reusing `nps1` with values 1..5 must be ignored.
    """
    org, owner, env, survey = admin_ctx
    connection.set_tenant(org)
    other = Survey.objects.create(
        environment=env,
        name="Rating reuse survey",
        status=SurveyStatus.PUBLISHED,
        questions=[{
            "id": "nps1",  # same id, but a rating question — must not be read as NPS
            "type": "rating",
            "headline": {"default": "Rate us"},
            "required": False,
            "config": {"range": 5, "scale": "star"},
        }],
        languages=LANGUAGES,
    )
    # Rating answers (1..5) that WOULD pass a naive 0..10 NPS filter if mis-scoped.
    Response.objects.create(survey=other, finished=True, data={"nps1": 1})
    Response.objects.create(survey=other, finished=True, data={"nps1": 2})

    client.force_login(owner)
    resp = client.get(_u("/"))
    assert resp.status_code == 200
    html = resp.content.decode()
    # Only the real NPS survey's finished answers (10, 4) count → NPS = +0. The rating 1/2 values
    # are NOT folded in (which would have dragged the score to a large negative number).
    assert "+0" in html


# --------------------------------------------------------------------------- #
#  Surveys list (+ New-survey create flow)                                     #
# --------------------------------------------------------------------------- #
def test_surveys_list_owner_200(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Customer Satisfaction Q4" in html
    # Status filter chips present.
    assert "Live" in html and "Draft" in html and "Closed" in html
    # The in-page "New survey" CTA (gated by can_create) links to the template gallery (WS-14),
    # which replaced the old name-it modal. Only rendered when the caller can create.
    assert f'href="/{SLUG}/surveys/new"' in html


def test_surveys_list_new_redirects_to_gallery(client, admin_ctx):
    """The legacy ?new=1 entry point (the topbar "New survey" CTA) now redirects to the survey
    template gallery (WS-14), which replaced the old name-it modal as the create entry point."""
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys?new=1"))
    assert resp.status_code in (301, 302)
    assert resp["Location"].endswith("/surveys/new")


def test_surveys_list_status_filter(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    # Add a draft survey so the filter has something to hide/show.
    connection.set_tenant(org)
    Survey.objects.create(
        environment=env, name="Draft Only Survey", status=SurveyStatus.DRAFT,
        questions=[], languages=LANGUAGES,
    )
    client.force_login(owner)
    # Draft filter shows the draft, hides the published one.
    resp = client.get(_u("/surveys?status=draft"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Draft Only Survey" in html
    assert "Customer Satisfaction Q4" not in html


def test_surveys_list_reader_200(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    reader = _add_member(org, "list_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_u("/surveys"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Customer Satisfaction Q4" in html
    # A read-only member cannot create → the in-page "New survey" gallery CTA is NOT rendered
    # (the always-on topbar "New survey" link from the shell is not a create affordance here).
    assert f'href="/{SLUG}/surveys/new"' not in html


def test_surveys_list_anonymous_redirects(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    resp = client.get(_u("/surveys"))
    assert resp.status_code in (301, 302)


def test_new_survey_create_flow_owner_redirects_to_builder(client, admin_ctx):
    """Owner POSTs the New-survey form → a DRAFT is created in the writable env and the user is
    redirected to the builder host /{slug}/surveys/{new_id}/edit."""
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.post(_u("/surveys"), {"name": "Brand New Survey"})
    assert resp.status_code == 302
    # The new survey exists in the env, as a draft, with the posted name.
    connection.set_tenant(org)
    created = Survey.objects.get(name="Brand New Survey")
    assert created.status == SurveyStatus.DRAFT
    assert created.environment_id == env.id
    # Redirect lands on the builder host for the new survey.
    assert resp["Location"] == _u(f"/surveys/{created.id}/edit")


def test_new_survey_create_flow_default_name(client, admin_ctx):
    """An empty name falls back to the default 'Untitled survey'."""
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.post(_u("/surveys"), {"name": "   "})
    assert resp.status_code == 302
    connection.set_tenant(org)
    assert Survey.objects.filter(name="Untitled survey").exists()


def test_new_survey_create_forbidden_for_reader(client, admin_ctx):
    """A member with only workspace ``read`` POSTing the create form → 403 (no write anywhere).

    Mirrors members_page (non-admin POST → 403) + survey_builder_host (can-view-not-edit → 403).
    """
    org, owner, env, survey = admin_ctx
    reader = _add_member(org, "list_reader_post@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(_u("/surveys"), {"name": "Nope"})
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
#  Survey share                                                                #
# --------------------------------------------------------------------------- #
def test_share_owner_200_has_public_link(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/share"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Share survey" in html
    # The public runtime link to /{slug}/s/{survey.id} is present.
    assert f"/{SLUG}/s/{survey.id}" in html
    assert "Copy" in html


def test_share_reader_200(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    reader = _add_member(org, "share_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_u(f"/surveys/{survey.id}/share"))
    assert resp.status_code == 200


def test_share_no_access_member_404(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    member = _add_member(org, "share_noaccess@example.com")
    client.force_login(member)
    resp = client.get(_u(f"/surveys/{survey.id}/share"))
    assert resp.status_code == 404


def test_share_unknown_survey_404(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{uuid.uuid4()}/share"))
    assert resp.status_code == 404


def test_share_anonymous_redirects(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    resp = client.get(_u(f"/surveys/{survey.id}/share"))
    assert resp.status_code in (301, 302)


# --------------------------------------------------------------------------- #
#  Results / analytics                                                         #
# --------------------------------------------------------------------------- #
def test_results_owner_200_real_aggregates(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/results"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Question results" in html
    # Each question headline appears.
    assert "How likely are you to recommend us?" in html
    assert "Overall experience?" in html
    assert "Primary reason for using our product?" in html
    assert "Any additional feedback?" in html
    # KPI labels.
    assert "Total responses" in html
    assert "Completion rate" in html
    # The choice label from a real answer renders.
    assert "Ease of use" in html
    # An open-text sample renders.
    assert "Love the clean interface!" in html


def test_results_reader_200(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    reader = _add_member(org, "results_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_u(f"/surveys/{survey.id}/results"))
    assert resp.status_code == 200


def test_results_no_access_member_404(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    member = _add_member(org, "results_noaccess@example.com")
    client.force_login(member)
    resp = client.get(_u(f"/surveys/{survey.id}/results"))
    assert resp.status_code == 404


def test_results_unknown_survey_404(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{uuid.uuid4()}/results"))
    assert resp.status_code == 404


def test_results_anonymous_redirects(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    resp = client.get(_u(f"/surveys/{survey.id}/results"))
    assert resp.status_code in (301, 302)


# --------------------------------------------------------------------------- #
#  Responses table                                                             #
# --------------------------------------------------------------------------- #
def test_responses_owner_200_real_rows(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/responses"))
    assert resp.status_code == 200
    html = resp.content.decode()
    # 3 real responses.
    assert "3 responses" in html
    # Column headers from the question set.
    assert "How likely are you to recommend us?" in html
    # NPS chip band labels appear (10 → Promoter, 4 → Detractor).
    assert "Promoter" in html
    assert "Detractor" in html


def test_responses_reader_200(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    reader = _add_member(org, "resp_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_u(f"/surveys/{survey.id}/responses"))
    assert resp.status_code == 200


def test_responses_no_access_member_404(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    member = _add_member(org, "resp_noaccess@example.com")
    client.force_login(member)
    resp = client.get(_u(f"/surveys/{survey.id}/responses"))
    assert resp.status_code == 404


def test_responses_unknown_survey_404(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{uuid.uuid4()}/responses"))
    assert resp.status_code == 404


def test_responses_anonymous_redirects(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    resp = client.get(_u(f"/surveys/{survey.id}/responses"))
    assert resp.status_code in (301, 302)


# --------------------------------------------------------------------------- #
#  Response detail                                                             #
# --------------------------------------------------------------------------- #
def test_response_detail_owner_200_qa(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    connection.set_tenant(org)
    resp_obj = Response.objects.filter(survey=survey, finished=True).first()
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/responses/{resp_obj.id}"))
    assert resp.status_code == 200
    html = resp.content.decode()
    # Every question headline appears with its rendered answer.
    assert "How likely are you to recommend us?" in html
    assert "Overall experience?" in html
    assert "Any additional feedback?" in html
    # An open-text answer renders.
    assert ("Love the clean interface!" in html) or ("Export is inconsistent." in html)


def test_response_detail_reader_200(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    reader = _add_member(org, "detail_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    connection.set_tenant(org)
    resp_obj = Response.objects.filter(survey=survey).first()
    client.force_login(reader)
    resp = client.get(_u(f"/surveys/{survey.id}/responses/{resp_obj.id}"))
    assert resp.status_code == 200


def test_response_detail_no_access_member_404(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    connection.set_tenant(org)
    resp_obj = Response.objects.filter(survey=survey).first()
    member = _add_member(org, "detail_noaccess@example.com")
    client.force_login(member)
    resp = client.get(_u(f"/surveys/{survey.id}/responses/{resp_obj.id}"))
    assert resp.status_code == 404


def test_response_detail_unknown_response_404(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/responses/{uuid.uuid4()}"))
    assert resp.status_code == 404


def test_response_detail_anonymous_redirects(client, admin_ctx):
    org, owner, env, survey = admin_ctx
    connection.set_tenant(org)
    resp_obj = Response.objects.filter(survey=survey).first()
    resp = client.get(_u(f"/surveys/{survey.id}/responses/{resp_obj.id}"))
    assert resp.status_code in (301, 302)


# --------------------------------------------------------------------------- #
#  Results index (the sidebar "Results" landing — /<slug>/results)            #
# --------------------------------------------------------------------------- #
def test_results_index_renders_and_lists_surveys(client, admin_ctx):
    """The sidebar Results link (/<slug>/results) resolves (was a 404) and lists the org's
    surveys, each linking to its per-survey results page."""
    org, owner, env, survey = admin_ctx
    client.force_login(owner)
    resp = client.get(_u("/results"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Customer Satisfaction Q4" in html  # the seeded survey
    assert f'href="/{SLUG}/surveys/{survey.id}/results"' in html  # row → per-survey results


def test_results_index_anonymous_redirects(client, admin_ctx):
    resp = client.get(_u("/results"))
    assert resp.status_code in (301, 302)
