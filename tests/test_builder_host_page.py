"""
Tests for the AUTHENTICATED survey-builder HOST page (Wave 3b).

  GET /{org_slug}/surveys/{survey_id}/edit → survey_builder_host

Hits the page through the real TenantSlugMiddleware path (slug resolves + activates the org
schema) with Django's session test ``Client`` + ``force_login`` (the page is a Django HTML
view gated by ``@login_required`` — NOT a DRF endpoint, so ``force_authenticate`` would not
drive the login gate). Org creation provisions a Postgres schema (DDL) → ``transactional_db``
+ ``force_drop`` cleanup, mirroring tests/test_surveys_api.py + tests/test_public_runtime_api.py.

Coverage (matches the brief's verify block):
  - 200 + the PRE-AGREED MOUNT CONTRACT for a user with ``edit_survey`` (owner; and an org
    member with workspace ``write``). Asserts #opensurvey-builder mounts with
    data-survey-id / data-org-slug / data-api-base / data-initial-survey — rendered regardless
    of ``vite_built`` (manifest absent in CI).
  - 403 for an org member with workspace ``read`` only (can view, cannot edit).
  - 404 for an org member with NO workspace access (cannot even view → no existence oracle),
    and for an unknown survey id.
  - 302 (redirect to LOGIN_URL) for an anonymous visitor (login gate present).
"""
import json
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, override_settings
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

User = get_user_model()

SLUG = "builder-org"

# A minimal but schema-shaped question set (mirrors the runtime-test fixtures). Created via
# the ORM with a draft status — the builder edits any non-terminal survey; status is irrelevant
# to the host page (it only gates on can(edit_survey)).
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


# --------------------------------------------------------------------------- #
#  Fixtures / helpers                                                          #
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
def builder_ctx(transactional_db):
    """Org + owner + workspace + env + a survey. Yields (org, owner, env, survey)."""
    owner = _mk_user("builder_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Builder Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = Survey.objects.create(
        environment=env,
        name="Builder Survey",
        status=SurveyStatus.DRAFT,
        questions=QUESTIONS,
        languages=LANGUAGES,
        styling={"brandColor": "#4f46e5"},
        endings=[{"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}}],
    )
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


def _edit_url(survey_id):
    return f"/{SLUG}/surveys/{survey_id}/edit"


# --------------------------------------------------------------------------- #
#  200 + MOUNT CONTRACT (edit_survey)                                          #
# --------------------------------------------------------------------------- #
def test_edit_page_renders_and_mounts_builder_island_for_owner(client, builder_ctx):
    """Owner (implicit manage ≥ write) gets 200 and the full PRE-AGREED mount contract."""
    org, owner, env, survey = builder_ctx
    client.force_login(owner)
    resp = client.get(_edit_url(survey.id))
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()

    # The builder mount point + the four fixed dataset attrs (the frontend builds to these).
    assert 'id="opensurvey-builder"' in html
    assert f'data-survey-id="{survey.id}"' in html
    assert f'data-org-slug="{SLUG}"' in html
    # data-api-base is the ROOT (/api/v1); the builder island appends /{orgSlug}/surveys/... itself
    # (authenticated CRUD, NOT the /public runtime base).
    assert 'data-api-base="/api/v1"' in html
    assert "/public" not in html  # must not point the island at the public runtime base
    # data-initial-survey carries the full survey definition for offline hydration.
    assert "data-initial-survey=" in html
    assert "Builder Survey" in html  # the survey name from the embedded definition JSON


def test_edit_page_initial_survey_is_the_full_definition(client, builder_ctx):
    """data-initial-survey is the openapi Survey shape (id/name/questions/styling/…), parseable.

    Django auto-escapes the JSON into the attribute; the browser un-escapes via
    el.dataset.initialSurvey before JSON.parse. The test un-escapes the HTML entities and
    asserts the round-trip yields the survey's fields.
    """
    org, owner, env, survey = builder_ctx
    client.force_login(owner)
    html = client.get(_edit_url(survey.id)).content.decode()

    import re
    from html import unescape

    m = re.search(r'data-initial-survey="([^"]*)"', html)
    assert m, "data-initial-survey attribute missing"
    initial = json.loads(unescape(m.group(1)))
    assert initial["id"] == str(survey.id)
    assert initial["name"] == "Builder Survey"
    assert [q["id"] for q in initial["questions"]] == ["q1"]
    assert initial["styling"] == {"brandColor": "#4f46e5"}
    assert initial["status"] == "draft"


def test_edit_page_member_with_write_allowed(client, builder_ctx):
    """An org member granted workspace `write` (via a team) can open the builder → 200."""
    org, owner, env, survey = builder_ctx
    writer = _add_member(org, "builder_writer@example.com")
    _grant(org, writer, env.workspace_id, WorkspaceAccess.WRITE)
    client.force_login(writer)
    resp = client.get(_edit_url(survey.id))
    assert resp.status_code == 200
    assert 'id="opensurvey-builder"' in resp.content.decode()


@override_settings(
    DJANGO_VITE={"default": {"dev_mode": False, "manifest_path": Path("/nonexistent/manifest.json")}}
)
def test_edit_page_mount_renders_without_vite_build(client, builder_ctx):
    """The mount div + data attrs render regardless of vite_built (forced False here).

    The vite_asset for main.tsx resolves only when a manifest is present; the host page must
    still emit the mount contract so the island can hydrate once the bundle loads. We force the
    no-build branch via override_settings (nonexistent manifest) so this is deterministic
    whether or not a frontend build exists on disk.
    """
    org, owner, env, survey = builder_ctx
    client.force_login(owner)
    html = client.get(_edit_url(survey.id)).content.decode()
    # The mount contract is present even though the manifest isn't built in the test env.
    assert 'id="opensurvey-builder"' in html
    assert f'data-survey-id="{survey.id}"' in html
    assert "data-initial-survey=" in html
    # The not-built hint is shown (proves we're on the vite_built=False branch in CI).
    assert "bundle not built yet" in html


# --------------------------------------------------------------------------- #
#  403 — can view but cannot edit (workspace read only)                        #
# --------------------------------------------------------------------------- #
def test_edit_page_member_read_only_forbidden(client, builder_ctx):
    """An org member with workspace `read` can SEE the survey but not edit → 403 (read < write)."""
    org, owner, env, survey = builder_ctx
    reader = _add_member(org, "builder_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_edit_url(survey.id))
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
#  404 — no visibility (no oracle) / unknown survey                            #
# --------------------------------------------------------------------------- #
def test_edit_page_member_no_access_404(client, builder_ctx):
    """An org member with NO workspace access cannot even view → 404 (no existence oracle).

    Distinct from the read-only 403 case: a caller who can't see the survey gets the same
    response as for a missing one (mirrors SurveyDetailView.get).
    """
    org, owner, env, survey = builder_ctx
    member = _add_member(org, "builder_noaccess@example.com")
    client.force_login(member)
    resp = client.get(_edit_url(survey.id))
    assert resp.status_code == 404


def test_edit_page_unknown_survey_404(client, builder_ctx):
    """An unknown survey id → 404 (the owner is fully privileged; this is a genuine miss)."""
    org, owner, env, survey = builder_ctx
    client.force_login(owner)
    resp = client.get(_edit_url(uuid.uuid4()))
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
#  302 — login gate (anonymous)                                               #
# --------------------------------------------------------------------------- #
def test_edit_page_anonymous_redirects_to_login(client, builder_ctx):
    """Anonymous visitor → @login_required redirects to LOGIN_URL (the auth gate is present)."""
    org, owner, env, survey = builder_ctx
    resp = client.get(_edit_url(survey.id))
    assert resp.status_code in (301, 302)
