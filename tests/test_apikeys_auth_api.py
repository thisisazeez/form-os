"""
HTTP-level tests for the `x-api-key` DRF authenticator (apps.apikeys.authentication).

A valid key authenticates a real API request (the creator's Membership drives RBAC, so the
surveys list endpoint returns 200); a REVOKED key and an INVALID/garbage key both 401; a key
issued for a DIFFERENT org cannot authenticate a request routed to this org (schema scoping).

Hits the real request path through TenantSlugMiddleware at `/api/v1/<slug>/...` so the
authenticator runs with the routed tenant schema active (exactly like production). Org creation
provisions a Postgres schema (DDL) → transactional_db + force_drop cleanup (tests/conftest.py).
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.test import APIClient

from apps.apikeys import services as apikey_services
from apps.organizations.models import Organization
from apps.organizations.services import create_organization
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "apikey-auth-org"
OTHER_SLUG = "apikey-auth-other"


@pytest.fixture
def api():
    return APIClient()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup(*slugs):
    connection.set_schema_to_public()
    for slug in slugs:
        for org in Organization.objects.filter(slug=slug):
            org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    """Org + owner + a production Environment. Yields (org, owner, env)."""
    owner = _mk_user("apikey_owner@example.com")
    _cleanup(SLUG, OTHER_SLUG)
    org = create_organization(name="ApiKey Auth Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
    _cleanup(SLUG, OTHER_SLUG)


# The surveys list endpoint requires IsAuthenticated + IsOrgMember — a clean probe for auth.
def _surveys_url(env):
    return f"/api/v1/{SLUG}/environments/{env.id}/surveys"


def test_valid_api_key_authenticates(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    _api_key, raw_key = apikey_services.create_api_key(
        environment=env, label="CI", created_by_user_id=owner.id
    )

    resp = api.get(_surveys_url(env), HTTP_X_API_KEY=raw_key)
    assert resp.status_code == 200


def test_revoked_api_key_is_401(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    api_key, raw_key = apikey_services.create_api_key(
        environment=env, label="CI", created_by_user_id=owner.id
    )
    apikey_services.revoke_api_key(api_key=api_key)

    resp = api.get(_surveys_url(env), HTTP_X_API_KEY=raw_key)
    assert resp.status_code == 401


def test_invalid_api_key_is_401(api, org_ctx):
    org, owner, env = org_ctx
    # A garbage key that was never issued.
    resp = api.get(_surveys_url(env), HTTP_X_API_KEY="osk_not_a_real_key_00000000")
    assert resp.status_code == 401


def test_missing_credentials_is_401(api, org_ctx):
    org, owner, env = org_ctx
    # No session, no key → the authenticate_header() makes DRF answer 401 (not 403).
    resp = api.get(_surveys_url(env))
    assert resp.status_code == 401


def test_api_key_scoped_to_its_own_org(api, org_ctx):
    """A key that only exists in org B's schema cannot authenticate a request routed to org A
    (schema scoping). We mint a key in a SEPARATE org and present it to THIS org's endpoint →
    401 (the hash doesn't exist in org A's schema)."""
    org, owner, env = org_ctx

    other_owner = _mk_user("apikey_other@example.com")
    other_org = create_organization(name="Other Org", slug=OTHER_SLUG, owner=other_owner)
    connection.set_tenant(other_org)
    other_ws = Workspace.objects.create(name="WS2", slug=f"ws-{uuid.uuid4().hex[:8]}")
    other_env = Environment.objects.create(workspace=other_ws, type=EnvType.PRODUCTION)
    _other_key, other_raw = apikey_services.create_api_key(
        environment=other_env, label="other", created_by_user_id=other_owner.id
    )

    # Present org B's key to org A's endpoint → not found in org A's schema → 401.
    resp = api.get(_surveys_url(env), HTTP_X_API_KEY=other_raw)
    assert resp.status_code == 401


def test_valid_key_bumps_last_used_at(api, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    api_key, raw_key = apikey_services.create_api_key(
        environment=env, label="CI", created_by_user_id=owner.id
    )
    assert api_key.last_used_at is None

    api.get(_surveys_url(env), HTTP_X_API_KEY=raw_key)

    connection.set_tenant(org)
    api_key.refresh_from_db()
    assert api_key.last_used_at is not None


def test_valid_key_authorizes_a_write_post(api, org_ctx):
    """A valid key authenticates a mutating POST (no CSRF blocks it — key auth wins before
    SessionAuthentication's CSRF enforcement). Creating a survey returns 201."""
    org, owner, env = org_ctx
    connection.set_tenant(org)
    _api_key, raw_key = apikey_services.create_api_key(
        environment=env, label="CI", created_by_user_id=owner.id
    )
    body = {
        "name": "Key-created survey",
        "environment_id": str(env.id),
        "questions": [
            {"id": "q1", "type": "openText", "headline": {"default": "Hi?"},
             "config": {"inputType": "long"}}
        ],
        "languages": [{"code": "en", "label": "English", "default": True}],
        "endings": [{"id": "e1", "type": "endScreen", "headline": {"default": "Thanks"}}],
    }
    resp = api.post(_surveys_url(env), body, format="json", HTTP_X_API_KEY=raw_key)
    assert resp.status_code == 201
