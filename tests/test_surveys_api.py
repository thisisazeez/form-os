"""
HTTP-level tests for the Survey CRUD + lifecycle API (contracts/openapi.yaml — Surveys tag).

Exercises the real request path through TenantSlugMiddleware: endpoints are hit at
``/api/v1/<slug>/...`` so the middleware resolves + activates the org schema and
``IsOrgMember`` + ``can()`` run against the routed org.

Org creation provisions a Postgres schema (DDL), so tests use ``transactional_db`` and clean
up with ``force_drop=True`` (same constraint as tests/conftest.py). Auth uses DRF
``force_authenticate``.

Covers: create (validation, env path-vs-body, RBAC write), list (filtered to readable
workspaces + status filter + response_count), detail get/put/delete (404-on-invisible,
403-on-unauthorized, validation on replace), status transitions (the frozen lifecycle matrix +
per-target RBAC), and the unique-id / backward-jump / bad-operator rejection on save.
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
from apps.surveys.models import Survey
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "survey-api-org"


# --------------------------------------------------------------------------- #
#  Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #
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
def org_ctx(transactional_db):
    """Org + owner + a workspace with a production Environment. Yields (org, owner, env)."""
    owner = _mk_user("survey_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Survey API Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
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


def _valid_survey_body(env_id, **overrides):
    body = {
        "name": "NPS Survey",
        "environment_id": str(env_id),
        "questions": [
            {
                "id": "nps",
                "type": "nps",
                "headline": {"default": "How likely are you to recommend us?"},
                "config": {},
                "logic": [
                    {
                        "id": "r1",
                        "condition": {"leftQuestionId": "nps", "operator": "lessThanOrEqual", "value": 6},
                        "action": {"type": "jumpTo", "target": "reason"},
                    }
                ],
            },
            {
                "id": "reason",
                "type": "openText",
                "headline": {"default": "Why?"},
                "config": {"inputType": "long"},
            },
        ],
        "languages": [{"code": "en", "label": "English", "default": True}],
        "endings": [{"id": "end1", "type": "endScreen", "headline": {"default": "Thanks!"}}],
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
#  CREATE  (POST /environments/{env}/surveys)                                  #
# --------------------------------------------------------------------------- #
def test_create_survey_as_owner(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys",
        _valid_survey_body(env.id),
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["name"] == "NPS Survey"
    assert body["status"] == "draft"  # always created as draft
    assert body["environment_id"] == str(env.id)
    assert [q["id"] for q in body["questions"]] == ["nps", "reason"]
    assert body["published_at"] is None


def test_create_survey_member_with_write_allowed(api, org_ctx):
    org, owner, env = org_ctx
    writer = _add_member(org, "survey_writer@example.com")
    _grant(org, writer, env.workspace_id, WorkspaceAccess.WRITE)
    api.force_authenticate(user=writer)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    )
    assert resp.status_code == 201


def test_create_survey_member_read_only_forbidden(api, org_ctx):
    org, owner, env = org_ctx
    reader = _add_member(org, "survey_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    )
    assert resp.status_code == 403  # read < write


def test_create_survey_no_access_forbidden(api, org_ctx):
    org, owner, env = org_ctx
    member = _add_member(org, "survey_noaccess@example.com")
    api.force_authenticate(user=member)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    )
    assert resp.status_code == 403


def test_create_survey_body_env_mismatch_400(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys",
        _valid_survey_body(env.id, environment_id=str(uuid.uuid4())),
        format="json",
    )
    assert resp.status_code == 400
    assert "environment_id" in str(resp.content)


def test_create_survey_duplicate_ids_400(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _valid_survey_body(env.id)
    body["questions"][1]["id"] = "nps"  # duplicate id
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400
    payload = resp.json()
    # in-code logic error surfaced under errors.logic with code duplicate_id
    assert any(e["code"] == "duplicate_id" for e in payload["errors"]["logic"])


def test_create_survey_backward_jump_400(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _valid_survey_body(env.id)
    # Make the second question jump back to the first.
    body["questions"][1]["logic"] = [
        {
            "id": "back",
            "condition": {"leftQuestionId": "reason", "operator": "isSubmitted"},
            "action": {"type": "jumpTo", "target": "nps"},
        }
    ]
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400
    assert any(e["code"] == "backward_jump" for e in resp.json()["errors"]["logic"])


def test_create_survey_bad_operator_type_400(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _valid_survey_body(env.id)
    # `greaterThan` on the openText 'reason' question is invalid for type. Reference it from nps.
    body["questions"][0]["logic"] = [
        {
            "id": "bad",
            "condition": {"leftQuestionId": "nps", "operator": "contains", "value": "x"},  # text op on nps
            "action": {"type": "jumpTo", "target": "reason"},
        }
    ]
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400
    assert any(e["code"] == "invalid_operator" for e in resp.json()["errors"]["logic"])


def test_create_survey_malformed_welcome_card_400(api, org_ctx):
    """The camelCase-translation path must reject a malformed welcome_card with schema_errors."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _valid_survey_body(env.id)
    body["welcome_card"] = {"enabled": "yes", "bogus": 1}  # enabled must be bool; bogus unexpected
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400
    assert resp.json().get("schema_errors"), "malformed welcome_card must surface schema_errors"


# --------------------------------------------------------------------------- #
#  LIST  (GET /environments/{env}/surveys)                                     #
# --------------------------------------------------------------------------- #
def test_list_surveys_paginated_with_response_count(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    for _ in range(3):
        api.post(
            f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
        )
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/surveys")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"count", "page", "page_size", "results"}
    assert body["count"] == 3
    assert all("response_count" in r for r in body["results"])
    # Summary omits the heavy blobs.
    assert all("questions" not in r for r in body["results"])


def test_list_surveys_status_filter(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    s = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    ).json()
    api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "published"}, format="json")
    api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    )
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/surveys?status=published")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["results"][0]["status"] == "published"


def test_list_surveys_member_no_access_empty(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    )
    member = _add_member(org, "survey_list_noacc@example.com")
    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/surveys")
    # Default-deny: no access to the workspace → empty page (NOT 403).
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


# --------------------------------------------------------------------------- #
#  DETAIL  (GET/PUT/DELETE /surveys/{id})                                      #
# --------------------------------------------------------------------------- #
def _create(api, env, owner):
    api.force_authenticate(user=owner)
    return api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    ).json()


def test_get_survey_full_definition(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    resp = api.get(f"/api/v1/{SLUG}/surveys/{s['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == s["id"]
    assert [q["id"] for q in body["questions"]] == ["nps", "reason"]
    assert body["endings"][0]["id"] == "end1"


def test_get_survey_invisible_404(api, org_ctx):
    """A member with no workspace access gets 404 (no existence oracle), not 403."""
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    member = _add_member(org, "survey_get_noacc@example.com")
    api.force_authenticate(user=member)
    resp = api.get(f"/api/v1/{SLUG}/surveys/{s['id']}")
    assert resp.status_code == 404


def test_put_survey_replace(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    body = _valid_survey_body(env.id, name="Renamed")
    resp = api.put(f"/api/v1/{SLUG}/surveys/{s['id']}", body, format="json")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


def test_put_survey_validation_rejected(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    body = _valid_survey_body(env.id)
    body["questions"][1]["id"] = "nps"  # duplicate
    resp = api.put(f"/api/v1/{SLUG}/surveys/{s['id']}", body, format="json")
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
#  BLOCKS overlay carried through create / replace (feat/parity-blocks)        #
# --------------------------------------------------------------------------- #
def test_create_survey_carries_blocks(api, org_ctx):
    """The additive `blocks` overlay round-trips through create + the full Survey shape."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _valid_survey_body(
        env.id,
        blocks=[
            {"id": "b1", "title": "Group A", "questionIds": ["nps", "reason"], "randomize": "all"}
        ],
    )
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 201, resp.content
    got = resp.json()["blocks"]
    assert got == [
        {"id": "b1", "title": "Group A", "questionIds": ["nps", "reason"], "randomize": "all"}
    ]
    # Persisted on the column.
    connection.set_tenant(org)
    assert Survey.objects.get(pk=resp.json()["id"]).blocks == got


def test_create_survey_without_blocks_defaults_empty(api, org_ctx):
    """Omitting `blocks` (every pre-existing survey) persists [] — flat behavior unchanged."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["blocks"] == []


def test_create_survey_invalid_block_ref_400(api, org_ctx):
    """A block referencing an unknown question id is rejected on save (cross-ref check)."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _valid_survey_body(
        env.id, blocks=[{"id": "b1", "questionIds": ["nps", "ghost"]}]
    )
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400, resp.content


def test_create_survey_question_in_two_blocks_400(api, org_ctx):
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _valid_survey_body(
        env.id,
        blocks=[
            {"id": "b1", "questionIds": ["nps"]},
            {"id": "b2", "questionIds": ["nps", "reason"]},  # nps double-claimed
        ],
    )
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400, resp.content


def test_put_survey_replaces_blocks(api, org_ctx):
    """PUT replaces the blocks overlay (PUT is full-replace semantics)."""
    org, owner, env = org_ctx
    s = _create(api, env, owner)  # created with no blocks
    body = _valid_survey_body(
        env.id, blocks=[{"id": "b1", "questionIds": ["nps"], "randomize": "exceptLast"}]
    )
    resp = api.put(f"/api/v1/{SLUG}/surveys/{s['id']}", body, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.json()["blocks"] == [
        {"id": "b1", "questionIds": ["nps"], "randomize": "exceptLast"}
    ]
    # Replacing again with no blocks clears them back to [].
    resp2 = api.put(f"/api/v1/{SLUG}/surveys/{s['id']}", _valid_survey_body(env.id), format="json")
    assert resp2.status_code == 200
    assert resp2.json()["blocks"] == []


# --------------------------------------------------------------------------- #
#  VARIABLES + calculate action carried through create / replace               #
#  (feat/parity-variables)                                                     #
# --------------------------------------------------------------------------- #
def _variables_survey_body(env_id, **overrides):
    """A valid survey with a number variable + a `calculate` action that mutates it."""
    body = _valid_survey_body(env_id)
    body["variables"] = [{"id": "score", "name": "Score", "type": "number", "initialValue": 0}]
    # Attach a calculate action to the nps question's logic (in addition to its jumpTo).
    body["questions"][0]["logic"].append(
        {
            "id": "rcalc",
            "condition": {"leftQuestionId": "nps", "operator": "isSubmitted"},
            "action": {
                "type": "calculate",
                "variableId": "score",
                "operation": "add",
                "value": {"type": "ref", "ref": "nps"},
            },
        }
    )
    body.update(overrides)
    return body


def test_create_survey_carries_variables(api, org_ctx):
    """The additive `variables` array + a `calculate` action round-trip through create."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys",
        _variables_survey_body(env.id),
        format="json",
    )
    assert resp.status_code == 201, resp.content
    got = resp.json()["variables"]
    assert got == [{"id": "score", "name": "Score", "type": "number", "initialValue": 0}]
    connection.set_tenant(org)
    assert Survey.objects.get(pk=resp.json()["id"]).variables == got


def test_create_survey_without_variables_defaults_empty(api, org_ctx):
    """Omitting `variables` (every pre-existing survey) persists [] — behavior unchanged."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", _valid_survey_body(env.id), format="json"
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["variables"] == []


def test_create_survey_calculate_unknown_variable_400(api, org_ctx):
    """A calculate action targeting an undeclared variable is rejected (cross-ref check)."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _variables_survey_body(env.id)
    body["questions"][0]["logic"][-1]["action"]["variableId"] = "ghost"  # not declared
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400, resp.content


def test_create_survey_calculate_type_incompatible_op_400(api, org_ctx):
    """`concat` on a NUMBER variable is rejected (type-compat check)."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _variables_survey_body(env.id)
    body["questions"][0]["logic"][-1]["action"]["operation"] = "concat"  # number var, text op
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400, resp.content


def test_create_survey_variable_id_collision_400(api, org_ctx):
    """A variable id colliding with a question id is a duplicate_id (shared namespace)."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)
    body = _valid_survey_body(env.id)
    body["variables"] = [{"id": "nps", "name": "Clash", "type": "number", "initialValue": 0}]
    resp = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json"
    )
    assert resp.status_code == 400, resp.content


def test_put_survey_replaces_variables(api, org_ctx):
    """PUT replaces the variables array (PUT is full-replace semantics)."""
    org, owner, env = org_ctx
    s = _create(api, env, owner)  # created with no variables
    resp = api.put(
        f"/api/v1/{SLUG}/surveys/{s['id']}", _variables_survey_body(env.id), format="json"
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["variables"] == [
        {"id": "score", "name": "Score", "type": "number", "initialValue": 0}
    ]
    # Replacing again with no variables clears them back to [].
    resp2 = api.put(
        f"/api/v1/{SLUG}/surveys/{s['id']}", _valid_survey_body(env.id), format="json"
    )
    assert resp2.status_code == 200
    assert resp2.json()["variables"] == []


def test_put_survey_writer_allowed_reader_forbidden(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    reader = _add_member(org, "survey_put_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.put(f"/api/v1/{SLUG}/surveys/{s['id']}", _valid_survey_body(env.id), format="json")
    assert resp.status_code == 403  # read can view but not edit

    writer = _add_member(org, "survey_put_writer@example.com")
    _grant(org, writer, env.workspace_id, WorkspaceAccess.WRITE)
    api.force_authenticate(user=writer)
    resp = api.put(f"/api/v1/{SLUG}/surveys/{s['id']}", _valid_survey_body(env.id), format="json")
    assert resp.status_code == 200


def test_delete_survey_requires_manage(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    # write-level member cannot delete (needs manage).
    writer = _add_member(org, "survey_del_writer@example.com")
    _grant(org, writer, env.workspace_id, WorkspaceAccess.WRITE)
    api.force_authenticate(user=writer)
    assert api.delete(f"/api/v1/{SLUG}/surveys/{s['id']}").status_code == 403

    # manage-level member can delete.
    manager = _add_member(org, "survey_del_manager@example.com")
    _grant(org, manager, env.workspace_id, WorkspaceAccess.MANAGE)
    api.force_authenticate(user=manager)
    assert api.delete(f"/api/v1/{SLUG}/surveys/{s['id']}").status_code == 204
    connection.set_tenant(org)
    assert not Survey.objects.filter(pk=s["id"]).exists()


# --------------------------------------------------------------------------- #
#  STATUS transitions  (POST /surveys/{id}/status)                             #
# --------------------------------------------------------------------------- #
def test_publish_sets_published_at(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    resp = api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "published"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"
    assert resp.json()["published_at"] is not None


def test_publish_pause_republish_keeps_published_at(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    pub = api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "published"}, format="json").json()
    first_published_at = pub["published_at"]
    api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "paused"}, format="json")
    re = api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "published"}, format="json").json()
    # published_at is the FIRST publish time and is not reset on republish.
    assert re["published_at"] == first_published_at


def test_invalid_transition_draft_to_paused_400(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)  # draft
    resp = api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "paused"}, format="json")
    assert resp.status_code == 400  # draft -> paused not allowed


def test_closed_is_terminal_400(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "published"}, format="json")
    api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "closed"}, format="json")
    # closed -> published is rejected (terminal).
    resp = api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "published"}, format="json")
    assert resp.status_code == 400


def test_no_op_same_status_400(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)  # draft
    resp = api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "draft"}, format="json")
    # draft is not a valid status-endpoint target (no edge into draft) → 400.
    assert resp.status_code == 400


def test_status_transition_requires_write(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    reader = _add_member(org, "survey_status_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    api.force_authenticate(user=reader)
    resp = api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "published"}, format="json")
    assert resp.status_code == 403  # publish_survey is write-level


def test_status_unknown_target_400(api, org_ctx):
    org, owner, env = org_ctx
    s = _create(api, env, owner)
    resp = api.post(f"/api/v1/{SLUG}/surveys/{s['id']}/status", {"status": "bogus"}, format="json")
    assert resp.status_code == 400  # serializer ChoiceField rejects


def test_survey_endpoints_require_auth(api, org_ctx):
    org, owner, env = org_ctx
    resp = api.get(f"/api/v1/{SLUG}/environments/{env.id}/surveys")
    assert resp.status_code in (401, 403)


# --------------------------------------------------------------------------- #
#  capture_ip round-trip (Settings tab — model/serializer/service threading)  #
# --------------------------------------------------------------------------- #
def test_capture_ip_round_trips_through_create_and_replace(api, org_ctx):
    """capture_ip defaults False, persists on create when set, and toggles on PUT."""
    org, owner, env = org_ctx
    api.force_authenticate(user=owner)

    # Default create → False.
    created = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys",
        _valid_survey_body(env.id),
        format="json",
    ).json()
    assert created["capture_ip"] is False

    # Create with capture_ip=True → echoed True.
    created_on = api.post(
        f"/api/v1/{SLUG}/environments/{env.id}/surveys",
        _valid_survey_body(env.id, capture_ip=True),
        format="json",
    ).json()
    assert created_on["capture_ip"] is True

    # PUT replace toggling it back off → echoed False.
    replaced = api.put(
        f"/api/v1/{SLUG}/surveys/{created_on['id']}",
        _valid_survey_body(env.id, capture_ip=False),
        format="json",
    ).json()
    assert replaced["capture_ip"] is False
