"""
Project-level "Look & Feel" theme (≈ Formbricks Configuration → Look & Feel).

A workspace carries a ``survey_theme`` JSONField — the SAME #/$defs/styling shape a survey uses
— which becomes the DEFAULT styling for every survey in the workspace, overridable per-survey.

Coverage:
  - merge_styling (unit): survey keys win; project fills gaps; empty+empty == {} (unchanged);
    top-level shadowing (a survey ``background`` shadows the project ``background`` WHOLE — no
    deep merge); None inputs treated as {}; inputs not mutated.
  - validate_theme (unit): {} valid; a full valid theme passes; unknown key / out-of-range
    roundness / bad enum rejected.
  - Look & Feel settings page (HTTP): owner GET 200; owner POST saves the theme (round-trips to
    the model); a bad theme POST → re-renders with an error and does NOT persist.
  - RBAC: a workspace-read-only member → 403 (can view, cannot manage); a no-access member → 404
    (no existence oracle). Mirrors workspace_settings_page layering.
  - PublicSurveySerializer inheritance: the served ``styling`` is survey-over-project (survey
    keys win, project fills the rest); default (no project theme + no survey styling) == {}.
  - Builder host page: the project theme is handed to the island SEPARATELY (data-project-theme)
    and the raw survey styling stays in data-initial-survey (no round-trip baking).

Org creation provisions a Postgres schema (DDL) → ``transactional_db`` + ``force_drop`` cleanup,
mirroring tests/test_builder_host_page.py.
"""
import json
import re
from html import unescape

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.surveys.public_serializers import PublicSurveySerializer
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace
from apps.workspaces.theme import merge_styling, validate_theme

User = get_user_model()

SLUG = "lookfeel-org"

QUESTIONS = [
    {
        "id": "q1",
        "type": "openText",
        "headline": {"default": "Your name?"},
        "required": True,
        "config": {"inputType": "short"},
    },
]
LANGUAGES = [{"code": "en", "label": "English", "default": True}]


# =========================================================================== #
#  merge_styling — the inheritance merge (pure unit; no DB)                    #
# =========================================================================== #
def test_merge_empty_both_is_empty_unchanged():
    """No project theme + no survey styling → {} (current behavior, unchanged)."""
    assert merge_styling({}, {}) == {}
    assert merge_styling(None, None) == {}


def test_merge_project_only_fills_when_survey_empty():
    project = {"brandColor": "#111", "roundness": 8}
    assert merge_styling(project, {}) == {"brandColor": "#111", "roundness": 8}


def test_merge_survey_only_passes_through_when_no_project():
    survey = {"brandColor": "#abc", "hideProgressBar": True}
    assert merge_styling({}, survey) == {"brandColor": "#abc", "hideProgressBar": True}


def test_merge_survey_overrides_project_and_project_fills_gaps():
    project = {"brandColor": "#proj", "questionColor": "#q", "roundness": 4}
    survey = {"brandColor": "#surv", "buttonColor": "#btn"}
    out = merge_styling(project, survey)
    # survey keys win; project fills the rest
    assert out == {
        "brandColor": "#surv",   # survey overrode project
        "questionColor": "#q",   # filled from project
        "roundness": 4,          # filled from project
        "buttonColor": "#btn",   # survey-only
    }


def test_merge_background_is_shadowed_whole_not_deep_merged():
    """A survey ``background`` (nested {type,value}) shadows the project ``background`` ENTIRELY."""
    project = {"background": {"type": "image", "value": "https://x/img.png"}}
    survey = {"background": {"type": "color", "value": "#fff"}}
    out = merge_styling(project, survey)
    assert out["background"] == {"type": "color", "value": "#fff"}  # survey's whole, no merge
    # The project's image value is NOT present anywhere in the result.
    assert "https://x/img.png" not in json.dumps(out)


def test_merge_does_not_mutate_inputs():
    project = {"brandColor": "#p"}
    survey = {"buttonColor": "#b"}
    merge_styling(project, survey)
    assert project == {"brandColor": "#p"}
    assert survey == {"buttonColor": "#b"}


# =========================================================================== #
#  validate_theme — #/$defs/styling shape (pure unit; no DB)                  #
# =========================================================================== #
def test_validate_theme_empty_ok():
    validate_theme({})  # no raise


def test_validate_theme_full_valid_ok():
    validate_theme(
        {
            "brandColor": "#2563eb",
            "questionColor": "#1e293b",
            "backgroundColor": "#ffffff",
            "buttonColor": "#2563eb",
            "buttonTextColor": "#ffffff",
            "background": {"type": "color", "value": "#f8fafc"},
            "logoUrl": "https://example.com/logo.png",
            "roundness": 8,
            "hideProgressBar": True,
            "cardArrangement": "casual",
        }
    )  # no raise


def test_validate_theme_unknown_key_rejected():
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        validate_theme({"notAStylingKey": "x"})


def test_validate_theme_out_of_range_roundness_rejected():
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        validate_theme({"roundness": 99})


def test_validate_theme_bad_card_arrangement_rejected():
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        validate_theme({"cardArrangement": "diagonal"})


# =========================================================================== #
#  Fixtures / helpers (DB)                                                    #
# =========================================================================== #
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
def ctx(transactional_db):
    """Org + owner + workspace + prod env + a published survey. Yields a dict."""
    owner = _mk_user("lf_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="LookFeel Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = Survey.objects.create(
        environment=env,
        name="LF Survey",
        status=SurveyStatus.PUBLISHED,
        questions=QUESTIONS,
        languages=LANGUAGES,
        styling={"brandColor": "#surveyred"},
        endings=[{"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}}],
    )
    yield {"org": org, "owner": owner, "ws": ws, "env": env, "survey": survey}
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


def _lf_url(ws_id):
    return f"/{SLUG}/workspaces/{ws_id}/settings/look-and-feel"


# =========================================================================== #
#  Look & Feel settings page — CRUD                                           #
# =========================================================================== #
def test_look_and_feel_page_renders_for_owner(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.get(_lf_url(ctx["ws"].id))
    assert resp.status_code == 200, resp.content
    assert b"Look" in resp.content  # the page heading


def test_look_and_feel_default_route_renders_for_owner(client, ctx):
    """The default-workspace route (no id) resolves to the first workspace."""
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/settings/workspace/look-and-feel")
    assert resp.status_code == 200


def test_look_and_feel_post_saves_theme(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.post(
        _lf_url(ctx["ws"].id),
        {
            "brandColor": "#123456",
            "questionColor": "",
            "roundness": "12",
            "cardArrangement": "casual",
            "hideProgressBar": "on",
            "backgroundType": "color",
            "backgroundValue": "#f8fafc",
            "logoUrl": "https://example.com/logo.png",
        },
    )
    assert resp.status_code == 200
    connection.set_tenant(ctx["org"])
    ws = Workspace.objects.get(pk=ctx["ws"].id)
    assert ws.survey_theme == {
        "brandColor": "#123456",
        "roundness": 12,
        "cardArrangement": "casual",
        "hideProgressBar": True,
        "background": {"type": "color", "value": "#f8fafc"},
        "logoUrl": "https://example.com/logo.png",
    }
    # An empty field is NOT written as "" — it is simply absent (falls back to default/parent).
    assert "questionColor" not in ws.survey_theme


def test_look_and_feel_post_invalid_theme_rejected_and_not_persisted(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.post(_lf_url(ctx["ws"].id), {"roundness": "999"})  # > max 32
    assert resp.status_code == 200
    assert b"32" in resp.content or b"Invalid" in resp.content  # the validation error surfaced
    connection.set_tenant(ctx["org"])
    ws = Workspace.objects.get(pk=ctx["ws"].id)
    assert ws.survey_theme == {}  # nothing persisted


# =========================================================================== #
#  Look & Feel settings page — RBAC                                           #
# =========================================================================== #
def test_look_and_feel_read_only_member_forbidden(client, ctx):
    """Workspace-read member can VIEW the workspace but not MANAGE it → 403."""
    reader = _add_member(ctx["org"], "lf_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_lf_url(ctx["ws"].id))
    assert resp.status_code == 403


def test_look_and_feel_no_access_member_404(client, ctx):
    """A member with NO workspace access cannot even view → 404 (no existence oracle)."""
    member = _add_member(ctx["org"], "lf_noaccess@example.com")
    client.force_login(member)
    resp = client.get(_lf_url(ctx["ws"].id))
    assert resp.status_code == 404


def test_look_and_feel_member_with_manage_can_save(client, ctx):
    """Workspace `manage` (not just org owner) qualifies — POST persists."""
    manager = _add_member(ctx["org"], "lf_manager@example.com")
    _grant(ctx["org"], manager, ctx["ws"].id, WorkspaceAccess.MANAGE)
    client.force_login(manager)
    resp = client.post(_lf_url(ctx["ws"].id), {"brandColor": "#0a0a0a"})
    assert resp.status_code == 200
    connection.set_tenant(ctx["org"])
    assert Workspace.objects.get(pk=ctx["ws"].id).survey_theme == {"brandColor": "#0a0a0a"}


# =========================================================================== #
#  PublicSurveySerializer — inheritance at serve time                         #
# =========================================================================== #
def test_public_serializer_survey_overrides_project_theme(ctx):
    """Served styling = survey-over-project: survey keys win, project fills the gaps."""
    connection.set_tenant(ctx["org"])
    ws = ctx["ws"]
    ws.survey_theme = {"brandColor": "#projblue", "questionColor": "#projq", "roundness": 6}
    ws.save(update_fields=["survey_theme"])
    survey = Survey.objects.select_related("environment", "environment__workspace").get(
        pk=ctx["survey"].id
    )
    data = PublicSurveySerializer(survey).data
    assert data["styling"] == {
        "brandColor": "#surveyred",  # survey wins over project's #projblue
        "questionColor": "#projq",   # filled from project
        "roundness": 6,              # filled from project
    }


def test_public_serializer_no_project_theme_and_no_survey_styling_is_empty(ctx):
    """Default: no project theme + no survey styling → {} (current behavior unchanged)."""
    connection.set_tenant(ctx["org"])
    s = ctx["survey"]
    s.styling = {}
    s.save(update_fields=["styling"])
    survey = Survey.objects.select_related("environment", "environment__workspace").get(pk=s.id)
    assert PublicSurveySerializer(survey).data["styling"] == {}


def test_public_serializer_project_theme_fills_when_survey_unstyled(ctx):
    """Survey with no styling inherits the FULL project theme."""
    connection.set_tenant(ctx["org"])
    ws = ctx["ws"]
    ws.survey_theme = {"brandColor": "#projonly", "roundness": 10}
    ws.save(update_fields=["survey_theme"])
    s = ctx["survey"]
    s.styling = {}
    s.save(update_fields=["styling"])
    survey = Survey.objects.select_related("environment", "environment__workspace").get(pk=s.id)
    assert PublicSurveySerializer(survey).data["styling"] == {
        "brandColor": "#projonly",
        "roundness": 10,
    }


# =========================================================================== #
#  Builder host page — project theme passed SEPARATELY (no baking)            #
# =========================================================================== #
def test_builder_host_keeps_raw_styling_and_passes_project_theme_separately(client, ctx):
    """data-initial-survey keeps the RAW survey styling; the project theme rides on
    data-project-theme — so the builder round-trip can never bake the theme into the survey."""
    connection.set_tenant(ctx["org"])
    ws = ctx["ws"]
    ws.survey_theme = {"brandColor": "#projblue", "roundness": 6}
    ws.save(update_fields=["survey_theme"])

    client.force_login(ctx["owner"])
    html = client.get(f"/{SLUG}/surveys/{ctx['survey'].id}/edit").content.decode()

    m_init = re.search(r'data-initial-survey="([^"]*)"', html)
    assert m_init, "data-initial-survey missing"
    initial = json.loads(unescape(m_init.group(1)))
    # RAW survey styling — NOT merged with the project theme.
    assert initial["styling"] == {"brandColor": "#surveyred"}

    m_theme = re.search(r'data-project-theme="([^"]*)"', html)
    assert m_theme, "data-project-theme missing"
    project = json.loads(unescape(m_theme.group(1)))
    assert project == {"brandColor": "#projblue", "roundness": 6}


def test_builder_host_project_theme_empty_when_workspace_has_none(client, ctx):
    """No project theme → data-project-theme is an empty object (still present for the island)."""
    client.force_login(ctx["owner"])
    html = client.get(f"/{SLUG}/surveys/{ctx['survey'].id}/edit").content.decode()
    m_theme = re.search(r'data-project-theme="([^"]*)"', html)
    assert m_theme, "data-project-theme missing"
    assert json.loads(unescape(m_theme.group(1))) == {}
