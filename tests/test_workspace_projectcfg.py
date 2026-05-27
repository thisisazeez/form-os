"""
Workspace General config (recontact cooldown) + white-label "remove branding".

Two workspace-level settings (≈ Formbricks Configuration → General + the enterprise white-label
toggle), both stored on ``Workspace`` (migration 0003):

  - ``recontact_days`` (PositiveIntegerField, NULL = off): the cooldown before re-showing an app
    survey to the same respondent. Persisted + exposed for parity; link surveys don't consume it
    yet.
  - ``remove_branding`` (BooleanField, default False): when True the PUBLIC runtime hides the
    "Powered by OpenSurvey" footer. Threaded to the runtime via the host page (data-remove-branding
    / runtime_config), NOT via PublicSurvey.

Coverage:
  - update_workspace_general (unit): sets recontact_days (int / None / blank-string → None);
    rejects negatives + non-ints; sets remove_branding only when provided; never touches name.
  - Settings page (HTTP): owner GET 200 (renders the General + branding controls); owner POST
    (general section) saves name + recontact_days; owner POST (branding section) toggles
    remove_branding; an invalid recontact value re-renders with an error and does NOT persist.
  - RBAC: read-only member → 403; no-access member → 404 (no oracle); workspace-manage member can
    save. Mirrors workspace_settings_page / look_and_feel layering.
  - Runtime host threading: the public HTML host page emits data-remove-branding="true" when the
    workspace flag is set (and "false" otherwise), and the json_script runtime_config carries the
    same boolean.

Org creation provisions a Postgres schema (DDL) → ``transactional_db`` + ``force_drop`` cleanup,
mirroring tests/test_workspace_look_and_feel.py.
"""
import json
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace
from apps.workspaces.services import update_workspace_general

User = get_user_model()

SLUG = "projectcfg-org"

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
#  Fixtures / helpers (DB)                                                     #
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
    owner = _mk_user("pcfg_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="ProjectCfg Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = Survey.objects.create(
        environment=env,
        name="PC Survey",
        status=SurveyStatus.PUBLISHED,
        questions=QUESTIONS,
        languages=LANGUAGES,
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


def _settings_url(ws_id):
    return f"/{SLUG}/workspaces/{ws_id}/settings"


def _runtime_url(survey_id):
    return f"/{SLUG}/s/{survey_id}"


# =========================================================================== #
#  update_workspace_general — service (DB, no HTTP)                            #
# =========================================================================== #
def test_general_sets_recontact_days_int(ctx):
    connection.set_tenant(ctx["org"])
    update_workspace_general(workspace=ctx["ws"], recontact_days=7)
    assert Workspace.objects.get(pk=ctx["ws"].id).recontact_days == 7


def test_general_blank_and_none_clear_recontact(ctx):
    connection.set_tenant(ctx["org"])
    ws = ctx["ws"]
    update_workspace_general(workspace=ws, recontact_days=5)
    update_workspace_general(workspace=ws, recontact_days="")  # blank string → None
    assert Workspace.objects.get(pk=ws.id).recontact_days is None
    update_workspace_general(workspace=ws, recontact_days=5)
    update_workspace_general(workspace=ws, recontact_days=None)
    assert Workspace.objects.get(pk=ws.id).recontact_days is None


def test_general_zero_is_valid(ctx):
    connection.set_tenant(ctx["org"])
    update_workspace_general(workspace=ctx["ws"], recontact_days="0")
    assert Workspace.objects.get(pk=ctx["ws"].id).recontact_days == 0


def test_general_negative_rejected(ctx):
    connection.set_tenant(ctx["org"])
    with pytest.raises(ValidationError):
        update_workspace_general(workspace=ctx["ws"], recontact_days=-1)
    assert Workspace.objects.get(pk=ctx["ws"].id).recontact_days is None  # nothing persisted


def test_general_non_integer_rejected(ctx):
    connection.set_tenant(ctx["org"])
    with pytest.raises(ValidationError):
        update_workspace_general(workspace=ctx["ws"], recontact_days="abc")


def test_general_sets_remove_branding_only_when_provided(ctx):
    connection.set_tenant(ctx["org"])
    ws = ctx["ws"]
    # Not provided (None) → leaves the default (False) untouched.
    update_workspace_general(workspace=ws, recontact_days=None)
    assert Workspace.objects.get(pk=ws.id).remove_branding is False
    # Provided True → set.
    update_workspace_general(workspace=ws, recontact_days=None, remove_branding=True)
    assert Workspace.objects.get(pk=ws.id).remove_branding is True
    # A later general save that omits remove_branding must NOT reset it.
    update_workspace_general(workspace=ws, recontact_days=3)
    fresh = Workspace.objects.get(pk=ws.id)
    assert fresh.remove_branding is True
    assert fresh.recontact_days == 3


def test_general_does_not_touch_name(ctx):
    connection.set_tenant(ctx["org"])
    ws = ctx["ws"]
    original = ws.name
    update_workspace_general(workspace=ws, recontact_days=10)
    assert Workspace.objects.get(pk=ws.id).name == original


# =========================================================================== #
#  Settings page — General + branding (HTTP CRUD)                             #
# =========================================================================== #
def test_settings_page_renders_general_and_branding_for_owner(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.get(_settings_url(ctx["ws"].id))
    assert resp.status_code == 200, resp.content
    body = resp.content
    assert b"Cooldown period" in body
    assert b"recontact_days" in body
    assert b"Remove OpenSurvey branding" in body
    assert b"remove_branding" in body


def test_settings_general_post_saves_name_and_recontact(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.post(
        _settings_url(ctx["ws"].id),
        {"section": "general", "name": "Renamed WS", "recontact_days": "14"},
    )
    assert resp.status_code in (200, 302)
    connection.set_tenant(ctx["org"])
    ws = Workspace.objects.get(pk=ctx["ws"].id)
    assert ws.name == "Renamed WS"
    assert ws.recontact_days == 14


def test_settings_general_blank_recontact_clears(client, ctx):
    connection.set_tenant(ctx["org"])
    ctx["ws"].recontact_days = 9
    ctx["ws"].save(update_fields=["recontact_days"])
    client.force_login(ctx["owner"])
    resp = client.post(
        _settings_url(ctx["ws"].id),
        {"section": "general", "name": "WS", "recontact_days": ""},
    )
    assert resp.status_code in (200, 302)
    connection.set_tenant(ctx["org"])
    assert Workspace.objects.get(pk=ctx["ws"].id).recontact_days is None


def test_settings_invalid_recontact_rejected_not_persisted(client, ctx):
    client.force_login(ctx["owner"])
    resp = client.post(
        _settings_url(ctx["ws"].id),
        {"section": "general", "name": "WS", "recontact_days": "-3"},
    )
    assert resp.status_code == 200  # re-renders the form (no redirect)
    assert b"negative" in resp.content.lower() or b"invalid" in resp.content.lower()
    connection.set_tenant(ctx["org"])
    assert Workspace.objects.get(pk=ctx["ws"].id).recontact_days is None


def test_settings_general_rename_rolls_back_when_recontact_invalid(client, ctx):
    """The rename + recontact save is atomic: an invalid recontact value must NOT leave a
    half-applied rename committed (the rename rolls back with it)."""
    connection.set_tenant(ctx["org"])
    original_name = ctx["ws"].name
    client.force_login(ctx["owner"])
    resp = client.post(
        _settings_url(ctx["ws"].id),
        {"section": "general", "name": "Should Not Persist", "recontact_days": "-3"},
    )
    assert resp.status_code == 200  # re-renders with the error, no redirect
    connection.set_tenant(ctx["org"])
    ws = Workspace.objects.get(pk=ctx["ws"].id)
    assert ws.name == original_name  # rename rolled back
    assert ws.recontact_days is None


def test_settings_branding_post_toggles_remove_branding(client, ctx):
    client.force_login(ctx["owner"])
    # Turn it ON (checkbox present).
    resp = client.post(
        _settings_url(ctx["ws"].id), {"section": "branding", "remove_branding": "1"}
    )
    assert resp.status_code == 200
    connection.set_tenant(ctx["org"])
    assert Workspace.objects.get(pk=ctx["ws"].id).remove_branding is True
    # Turn it OFF (checkbox absent in the POST).
    resp = client.post(_settings_url(ctx["ws"].id), {"section": "branding"})
    assert resp.status_code == 200
    connection.set_tenant(ctx["org"])
    assert Workspace.objects.get(pk=ctx["ws"].id).remove_branding is False


def test_settings_branding_post_does_not_clobber_recontact(client, ctx):
    """Saving the branding form must leave recontact_days untouched (separate concern)."""
    connection.set_tenant(ctx["org"])
    ctx["ws"].recontact_days = 21
    ctx["ws"].save(update_fields=["recontact_days"])
    client.force_login(ctx["owner"])
    client.post(_settings_url(ctx["ws"].id), {"section": "branding", "remove_branding": "1"})
    connection.set_tenant(ctx["org"])
    ws = Workspace.objects.get(pk=ctx["ws"].id)
    assert ws.remove_branding is True
    assert ws.recontact_days == 21


# =========================================================================== #
#  Settings page — RBAC                                                       #
# =========================================================================== #
def test_settings_read_only_member_forbidden(client, ctx):
    reader = _add_member(ctx["org"], "pcfg_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    assert client.get(_settings_url(ctx["ws"].id)).status_code == 403


def test_settings_no_access_member_404(client, ctx):
    member = _add_member(ctx["org"], "pcfg_noaccess@example.com")
    client.force_login(member)
    assert client.get(_settings_url(ctx["ws"].id)).status_code == 404


def test_settings_manage_member_can_save(client, ctx):
    manager = _add_member(ctx["org"], "pcfg_manager@example.com")
    _grant(ctx["org"], manager, ctx["ws"].id, WorkspaceAccess.MANAGE)
    client.force_login(manager)
    resp = client.post(
        _settings_url(ctx["ws"].id), {"section": "branding", "remove_branding": "1"}
    )
    assert resp.status_code == 200
    connection.set_tenant(ctx["org"])
    assert Workspace.objects.get(pk=ctx["ws"].id).remove_branding is True


# =========================================================================== #
#  Runtime host page — remove_branding threading (data-remove-branding + config) #
# =========================================================================== #
def test_runtime_host_branding_false_by_default(client, ctx):
    """Default (branding shown): data-remove-branding="false" + runtime_config removeBranding false."""
    resp = client.get(_runtime_url(ctx["survey"].id))
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()
    assert 'data-remove-branding="false"' in html
    cfg = _runtime_config(html)
    assert cfg["removeBranding"] is False


def test_runtime_host_branding_true_when_workspace_flag_set(client, ctx):
    """White-labeled workspace: data-remove-branding="true" + runtime_config removeBranding true."""
    connection.set_tenant(ctx["org"])
    ctx["ws"].remove_branding = True
    ctx["ws"].save(update_fields=["remove_branding"])
    resp = client.get(_runtime_url(ctx["survey"].id))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-remove-branding="true"' in html
    cfg = _runtime_config(html)
    assert cfg["removeBranding"] is True


def _runtime_config(html):
    """Parse the #opensurvey-runtime-config json_script payload out of the host HTML."""
    m = re.search(
        r'<script id="opensurvey-runtime-config"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    assert m, "runtime_config json_script not found"
    return json.loads(m.group(1))
