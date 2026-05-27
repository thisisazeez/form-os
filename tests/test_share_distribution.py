"""
Tests for the WIRED share-distribution surface (WS-7): personalized links, QR code, web embed.

  POST /{org_slug}/surveys/{id}/share/links      survey_mint_links  (mint per-contact SurveyLinks)
  GET  /{org_slug}/surveys/{id}/share/links.csv  survey_links_csv   (name,email,personal_url)
  GET  /{org_slug}/surveys/{id}/share/qr         survey_qr          (PNG/SVG of the public link)
  GET  /{org_slug}/s/{id}                          public_runtime_host (X-Frame embed allowance)

What these assert (the brief's four deliverables):
  1. Personal-link minting is RBAC-gated (edit_survey/write) and idempotent, and a minted
     per-contact token RESOLVES through the EXISTING public runtime path
     (GET /api/v1/{slug}/public/surveys/{id}?link_token=...), not a new code path.
  2. The QR endpoint returns an image with the correct content-type (PNG + SVG).
  3. The web-embed page (public runtime) ships NO `X-Frame-Options: DENY` header so it can be
     iframed, while an authenticated admin page STILL ships DENY (clickjacking intact).
  4. CSV export streams name,email,personal_url for every link.

Real request path through TenantSlugMiddleware (slug resolves + activates the org schema);
Django session test ``Client`` + ``force_login`` for the admin (HTML) views, ``APIClient``
(anonymous) for the public runtime resolution. Org creation provisions a Postgres schema (DDL)
→ ``transactional_db`` + ``force_drop`` cleanup (mirrors tests/test_surveys_admin.py).
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from apps.contacts.services import upsert_contact
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.responses.models import SurveyLink
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "share-org"

QUESTIONS = [
    {
        "id": "q1",
        "type": "openText",
        "headline": {"default": "Your name?"},
        "required": False,
        "config": {"inputType": "short"},
    },
]
LANGUAGES = [{"code": "default", "label": "Default", "default": True}]


# --------------------------------------------------------------------------- #
#  Fixtures / helpers (mirror tests/test_surveys_admin.py)                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return Client()


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


def _make_survey(env, *, single_use=False):
    return Survey.objects.create(
        environment=env,
        name="Feedback Survey",
        status=SurveyStatus.PUBLISHED,
        questions=QUESTIONS,
        languages=LANGUAGES,
        styling={},
        endings=[],
        single_use=single_use,
    )


def _make_contact(env, *, name, email):
    contact, _ = upsert_contact(
        environment=env,
        attributes=[
            {"key": "name", "value": name},
            {"key": "email", "value": email},
        ],
    )
    return contact


@pytest.fixture
def ctx(transactional_db):
    """Org + owner + workspace + prod env + a PUBLISHED survey + two contacts.

    Yields (org, owner, env, survey, [contact1, contact2]).
    """
    owner = _mk_user("share_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Share Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="Main WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = _make_survey(env)
    c1 = _make_contact(env, name="Ada Lovelace", email="ada@example.com")
    c2 = _make_contact(env, name="Alan Turing", email="alan@example.com")
    yield org, owner, env, survey, [c1, c2]
    _cleanup(SLUG)


# --------------------------------------------------------------------------- #
#  1. Personalized links — RBAC + minting + idempotency                        #
# --------------------------------------------------------------------------- #
def test_mint_links_owner_creates_one_per_contact(client, ctx):
    org, owner, env, survey, contacts = ctx
    client.force_login(owner)
    resp = client.post(
        _u(f"/surveys/{survey.id}/share/links"), data={"target": "all"}
    )
    assert resp.status_code == 200, resp.content

    connection.set_tenant(org)
    links = SurveyLink.objects.filter(survey=survey)
    assert links.count() == 2
    # Each link is bound to a distinct contact (server-derived association).
    assert {link.contact_id for link in links} == {contacts[0].id, contacts[1].id}
    # The minted token + personal url appears in the re-rendered page.
    html = resp.content.decode()
    assert "ada@example.com" in html
    assert "alan@example.com" in html


def test_mint_links_is_idempotent(client, ctx):
    org, owner, env, survey, contacts = ctx
    client.force_login(owner)
    client.post(_u(f"/surveys/{survey.id}/share/links"), data={"target": "all"})
    client.post(_u(f"/surveys/{survey.id}/share/links"), data={"target": "all"})

    connection.set_tenant(org)
    # Re-minting does NOT duplicate live links (one per contact).
    assert SurveyLink.objects.filter(survey=survey).count() == 2


def test_mint_links_reader_403(client, ctx):
    org, owner, env, survey, contacts = ctx
    reader = _add_member(org, "share_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(
        _u(f"/surveys/{survey.id}/share/links"), data={"target": "all"}
    )
    assert resp.status_code == 403

    connection.set_tenant(org)
    assert SurveyLink.objects.filter(survey=survey).count() == 0


def test_mint_links_no_access_member_404(client, ctx):
    org, owner, env, survey, contacts = ctx
    member = _add_member(org, "share_noaccess@example.com")
    client.force_login(member)
    resp = client.post(
        _u(f"/surveys/{survey.id}/share/links"), data={"target": "all"}
    )
    # No existence oracle: an invisible survey is indistinguishable from a missing one.
    assert resp.status_code == 404


def test_minted_token_resolves_via_existing_runtime_path(client, api, ctx):
    """The load-bearing integration: a minted per-contact token resolves the contact through
    the EXISTING public runtime path (public_services.resolve_link), not a new code path."""
    org, owner, env, survey, contacts = ctx
    client.force_login(owner)
    client.post(_u(f"/surveys/{survey.id}/share/links"), data={"target": "all"})

    connection.set_tenant(org)
    link = SurveyLink.objects.filter(survey=survey, contact_id=contacts[0].id).get()

    # (a) The runtime HTML host accepts the token and threads it into the runtime config.
    host = client.get(_u(f"/s/{survey.id}?link_token={link.token}"))
    assert host.status_code == 200
    assert link.token in host.content.decode()

    # (b) The public survey-definition endpoint resolves the token through resolve_link:
    #     a valid token → 200, a garbage token → 410 (link_gone) on the SAME path.
    ok = api.get(_u(f"/api/v1/public/surveys/{survey.id}?link_token={link.token}"))
    assert ok.status_code == 200, ok.content
    gone = api.get(
        _u(f"/api/v1/public/surveys/{survey.id}?link_token=not-a-real-token")
    )
    assert gone.status_code == 410
    assert gone.json()["code"] == "link_gone"


def test_single_use_survey_requires_minted_link(client, api, transactional_db):
    """A single_use survey is un-openable without a link; the minted token opens it (410→200)."""
    owner = _mk_user("su_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="SU Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="WS", slug="ws-su")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = _make_survey(env, single_use=True)
    contact = _make_contact(env, name="Grace Hopper", email="grace@example.com")

    # No token → 410 ("needs a link") on the existing public definition path.
    no_token = api.get(_u(f"/api/v1/public/surveys/{survey.id}"))
    assert no_token.status_code == 410

    # Mint via the admin page, then the token opens it.
    client.force_login(owner)
    client.post(_u(f"/surveys/{survey.id}/share/links"), data={"target": "all"})
    connection.set_tenant(org)
    link = SurveyLink.objects.filter(survey=survey, contact_id=contact.id).get()
    ok = api.get(_u(f"/api/v1/public/surveys/{survey.id}?link_token={link.token}"))
    assert ok.status_code == 200, ok.content

    _cleanup(SLUG)


# --------------------------------------------------------------------------- #
#  2. CSV export                                                               #
# --------------------------------------------------------------------------- #
def test_links_csv_download(client, ctx):
    org, owner, env, survey, contacts = ctx
    client.force_login(owner)
    client.post(_u(f"/surveys/{survey.id}/share/links"), data={"target": "all"})

    resp = client.get(_u(f"/surveys/{survey.id}/share/links.csv"))
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/csv")
    assert "attachment" in resp["Content-Disposition"]
    body = resp.content.decode()
    # Header + both contacts (name, email, personal_url with link_token).
    assert body.splitlines()[0] == "name,email,personal_url"
    assert "ada@example.com" in body
    assert "alan@example.com" in body
    assert "link_token=" in body


def _flag_email_pii(env):
    """Mark the env's ``email`` attribute key as PII (a security-conscious deployment would)."""
    from apps.contacts.models import ContactAttributeKey

    ContactAttributeKey.objects.filter(environment=env, key="email").update(is_pii=True)


def test_csv_value_is_formula_injection_safe(client, ctx):
    """A contact name beginning with a formula trigger (=,+,-,@) is neutralized in the CSV."""
    org, owner, env, survey, contacts = ctx
    connection.set_tenant(org)
    _make_contact(env, name="=HYPERLINK(\"http://evil\")", email="mal@example.com")
    client.force_login(owner)
    client.post(_u(f"/surveys/{survey.id}/share/links"), data={"target": "all"})
    resp = client.get(_u(f"/surveys/{survey.id}/share/links.csv"))
    body = resp.content.decode()
    # The malicious cell is prefixed with a quote so a spreadsheet treats it as text.
    assert "'=HYPERLINK" in body
    # No bare leading "=HYPERLINK" cell survives (it's always quoted).
    assert ",=HYPERLINK" not in body


def test_links_csv_reader_allowed_but_pii_email_masked(client, ctx):
    """CSV is a READ surface (view_survey floor) — a workspace reader may download, but a
    PII-flagged email column is MASKED for read-only callers (same rule as the contacts list,
    via ``pii_unmasked_for_environment``)."""
    org, owner, env, survey, contacts = ctx
    connection.set_tenant(org)
    _flag_email_pii(env)
    # Owner mints the links first.
    client.force_login(owner)
    client.post(_u(f"/surveys/{survey.id}/share/links"), data={"target": "all"})

    reader = _add_member(org, "csv_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_u(f"/surveys/{survey.id}/share/links.csv"))
    assert resp.status_code == 200
    body = resp.content.decode()
    # The reader does NOT see raw emails (masked), but still gets the personal URLs (opaque).
    assert "ada@example.com" not in body
    assert "alan@example.com" not in body
    assert "***" in body
    assert "link_token=" in body


def test_links_csv_write_user_sees_raw_pii_email(client, ctx):
    """A workspace-WRITE caller sees raw emails even when the key is PII (unmasked on write)."""
    org, owner, env, survey, contacts = ctx
    connection.set_tenant(org)
    _flag_email_pii(env)
    writer = _add_member(org, "csv_writer@example.com")
    _grant(org, writer, env.workspace_id, WorkspaceAccess.WRITE)
    client.force_login(writer)
    # Write user mints + downloads.
    client.post(_u(f"/surveys/{survey.id}/share/links"), data={"target": "all"})
    resp = client.get(_u(f"/surveys/{survey.id}/share/links.csv"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "ada@example.com" in body
    assert "alan@example.com" in body


# --------------------------------------------------------------------------- #
#  3. QR endpoint                                                              #
# --------------------------------------------------------------------------- #
def test_qr_default_png(client, ctx):
    org, owner, env, survey, contacts = ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/share/qr"))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
    # A real PNG (magic bytes) with non-trivial size.
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(resp.content) > 100


def test_qr_svg(client, ctx):
    org, owner, env, survey, contacts = ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/share/qr?format=svg"))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/svg+xml"
    assert b"<svg" in resp.content


def test_qr_reader_allowed_unknown_404(client, ctx):
    org, owner, env, survey, contacts = ctx
    reader = _add_member(org, "qr_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    assert client.get(_u(f"/surveys/{survey.id}/share/qr")).status_code == 200
    # Unknown survey → 404 (no existence oracle).
    assert client.get(_u(f"/surveys/{uuid.uuid4()}/share/qr")).status_code == 404


# --------------------------------------------------------------------------- #
#  4. Web embed — X-Frame allowance ONLY on the public survey page             #
# --------------------------------------------------------------------------- #
def _xframe(resp):
    """The X-Frame-Options header value (or None if absent)."""
    return resp.headers.get("X-Frame-Options")


def test_public_runtime_page_is_iframe_embeddable(client, ctx):
    """The public survey page carries NO X-Frame-Options: DENY → it can be embedded."""
    org, owner, env, survey, contacts = ctx
    resp = client.get(_u(f"/s/{survey.id}"))
    assert resp.status_code == 200
    # @xframe_options_exempt removed the global DENY for this response only.
    assert _xframe(resp) != "DENY"


def test_admin_page_still_denies_framing(client, ctx):
    """An authenticated admin page (the share page) STILL ships DENY — clickjacking intact."""
    org, owner, env, survey, contacts = ctx
    client.force_login(owner)
    resp = client.get(_u(f"/surveys/{survey.id}/share"))
    assert resp.status_code == 200
    assert _xframe(resp) == "DENY"


def test_share_page_renders_embed_qr_and_email(client, ctx):
    """The share page wires the embed snippet, QR <img>, email snippet, and social links."""
    org, owner, env, survey, contacts = ctx
    client.force_login(owner)
    html = client.get(_u(f"/surveys/{survey.id}/share")).content.decode()
    # Web embed snippet references the real public URL.
    assert "iframe" in html
    assert f"/{SLUG}/s/{survey.id}" in html
    # QR image + downloads.
    assert f"/{SLUG}/surveys/{survey.id}/share/qr" in html
    # Email share + social-intent links.
    assert "Email share" in html
    assert "twitter.com/intent/tweet" in html
    assert "linkedin.com/sharing" in html
