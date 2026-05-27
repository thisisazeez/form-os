"""
Audit-log write + view + RBAC tests.

WRITE: the apps.audit.services.record helper appends an AuditLog row; mutation surfaces wire it
(here we assert the helper + an end-to-end survey-create-via-API write).
VIEW: the `/<org_slug>/settings/audit-log` page lists entries, paginates, filters, and hydrates
the actor; it is RBAC-gated to owner/admin (member → 403, anonymous → login redirect).

Server-rendered HTML views gated by @login_required → Django's session Client + force_login
(not DRF force_authenticate). Org creation provisions a schema (DDL) → transactional_db +
force_drop (conftest.py).
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "audit-log-org"


@pytest.fixture
def client():
    return Client()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True, name="Owner")


def _cleanup():
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=SLUG):
        org.delete(force_drop=True)


@pytest.fixture
def org_ctx(transactional_db):
    """Org + owner + a production Environment. Yields (org, owner, env)."""
    owner = _mk_user("audit_owner@example.com")
    _cleanup()
    org = create_organization(name="Audit Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    ws = Workspace.objects.create(name="WS", slug=f"ws-{uuid.uuid4().hex[:8]}")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    yield org, owner, env
    _cleanup()


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )
    return user


# --------------------------------------------------------------------------- #
#  WRITE                                                                       #
# --------------------------------------------------------------------------- #
def test_record_helper_appends_entry(org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    entry = audit.record(
        action=audit.ACTION_SURVEY_CREATED,
        target_type="survey",
        target_id="abc-123",
        actor_user_id=owner.id,
        metadata={"name": "X"},
    )
    assert entry is not None
    assert AuditLog.objects.filter(action="survey.created", target_id="abc-123").exists()


def test_survey_create_via_api_writes_audit(org_ctx):
    """Creating a survey through the API writes a survey.created audit entry with the actor."""
    org, owner, env = org_ctx
    api = APIClient()
    api.force_authenticate(owner)
    body = {
        "name": "Audited survey",
        "environment_id": str(env.id),
        "questions": [
            {"id": "q1", "type": "openText", "headline": {"default": "Hi?"},
             "config": {"inputType": "long"}}
        ],
        "languages": [{"code": "en", "label": "English", "default": True}],
        "endings": [{"id": "e1", "type": "endScreen", "headline": {"default": "Thanks"}}],
    }
    resp = api.post(f"/api/v1/{SLUG}/environments/{env.id}/surveys", body, format="json")
    assert resp.status_code == 201

    connection.set_tenant(org)
    entry = AuditLog.objects.filter(action="survey.created").order_by("-created_at").first()
    assert entry is not None
    assert str(entry.actor_user_id) == str(owner.id)


def test_survey_create_via_htmx_writes_audit(client, org_ctx):
    """The browser (HTMX) New-survey flow also writes a survey.created audit entry."""
    org, owner, env = org_ctx
    client.force_login(owner)
    # POST to the surveys list page with ?new=1 creates a draft and redirects to the builder.
    resp = client.post(f"/{SLUG}/surveys?new=1", {"name": "HTMX survey"})
    assert resp.status_code in (302, 303)

    connection.set_tenant(org)
    entry = (
        AuditLog.objects.filter(action="survey.created", metadata__via="htmx")
        .order_by("-created_at")
        .first()
    )
    assert entry is not None
    assert str(entry.actor_user_id) == str(owner.id)


# --------------------------------------------------------------------------- #
#  VIEW + RBAC                                                                 #
# --------------------------------------------------------------------------- #
def test_audit_page_owner_renders(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    audit.record(action="survey.published", target_type="survey", actor_user_id=owner.id)

    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/audit-log")
    assert resp.status_code == 200
    assert b"survey.published" in resp.content


def test_audit_page_member_403(client, org_ctx):
    org, owner, env = org_ctx
    member = _add_member(org, "audit_member@example.com", role=OrgRole.MEMBER)
    client.force_login(member)
    resp = client.get(f"/{SLUG}/settings/audit-log")
    assert resp.status_code == 403


def test_audit_page_anonymous_redirects(client, org_ctx):
    resp = client.get(f"/{SLUG}/settings/audit-log")
    assert resp.status_code in (301, 302)


def test_audit_page_action_filter(client, org_ctx):
    org, owner, env = org_ctx
    connection.set_tenant(org)
    audit.record(action="survey.published", target_type="survey", actor_user_id=owner.id)
    audit.record(action="webhook.created", target_type="webhook", actor_user_id=owner.id)

    client.force_login(owner)
    resp = client.get(f"/{SLUG}/settings/audit-log?action=webhook")
    assert resp.status_code == 200
    assert b"webhook.created" in resp.content
    assert b"survey.published" not in resp.content
