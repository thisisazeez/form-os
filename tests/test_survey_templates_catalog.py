"""
WS-14 — Survey Template Gallery: catalog + create-from-template + gallery filters.

Three concerns, three test groups:

  1. **Catalog integrity (the guard).** EVERY entry in ``apps.surveys.templates_catalog.CATALOG``
     is a FULL survey definition that MUST pass ``validate_survey_definition`` (jsonschema against
     contracts/survey-schema.json + the in-code logic checks). A broken template would otherwise
     only surface when a user clicks it and the create POST 500s/400s — this loop is the
     ship-gate. Pure-Python (no DB / tenant needed); fast.

  2. **The gallery page** (``GET /<slug>/surveys/new``): renders the "Create a new survey" page
     with the Start-from-scratch card, a card per template, the channel/industry/role filter
     chips, and search — and FILTERS server-side on ``?channel=&industry=&role=&q=`` query params.

  3. **Create-from-template** (``POST /<slug>/surveys/new`` with ``template=<slug>``): creates a
     DRAFT survey pre-populated with a DEEP COPY of the template's definition in the caller's
     writable environment, then redirects to the builder host. RBAC mirrors the existing
     start-from-scratch flow (workspace ``write`` required; a read-only member → 403). The blank
     "Start from scratch" path (``POST /<slug>/surveys``) still works unchanged.

Tenant/RBAC machinery mirrors tests/test_surveys_admin.py (real TenantSlugMiddleware path,
``transactional_db`` for the schema DDL, ``force_drop`` cleanup).
"""
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
from apps.surveys.templates_catalog import (
    CATALOG,
    CHANNELS,
    INDUSTRIES,
    ROLES,
    get_template,
    has_logic,
    template_definition,
)
from apps.surveys.validators import SurveyValidationError, validate_survey_definition
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "tpl-org"


# --------------------------------------------------------------------------- #
#  1. Catalog integrity — the ship-gate guard                                  #
# --------------------------------------------------------------------------- #
def test_catalog_is_non_trivial():
    """The catalog ships a realistic Formbricks-style set (>=18 templates per WS-14)."""
    assert len(CATALOG) >= 18, f"Catalog too small: {len(CATALOG)} templates"


@pytest.mark.parametrize("entry", CATALOG, ids=[e["slug"] for e in CATALOG])
def test_every_catalog_template_validates(entry):
    """EVERY catalog template's definition passes validate_survey_definition (schema + logic).

    This is the guard against shipping a broken template: a malformed config, a missing required
    i18n ``default`` key, a backward/forward logic ref, or an operator/type mismatch all raise
    SurveyValidationError here and fail the build BEFORE a user ever clicks the card.
    """
    definition = entry["definition"]
    try:
        validate_survey_definition(definition)
    except SurveyValidationError as exc:  # pragma: no cover - failure path detail
        pytest.fail(
            f"Template {entry['slug']!r} is invalid:\n"
            f"  schema_errors={exc.schema_errors}\n"
            f"  logic_errors={exc.logic_errors}"
        )


@pytest.mark.parametrize("entry", CATALOG, ids=[e["slug"] for e in CATALOG])
def test_every_catalog_entry_has_well_formed_metadata(entry):
    """Each entry carries the gallery metadata with values from the known filter vocabularies."""
    assert entry["slug"]
    assert entry["name"]
    assert entry["description"]
    assert entry["channel"] in CHANNELS, entry["channel"]
    assert entry["industry"] in INDUSTRIES, entry["industry"]
    assert entry["role"] in ROLES, entry["role"]
    # The definition name matches the entry name (the survey is created with this name).
    assert entry["definition"]["name"] == entry["name"]


def test_catalog_slugs_are_unique():
    slugs = [e["slug"] for e in CATALOG]
    assert len(slugs) == len(set(slugs))


def test_get_template_and_unknown():
    assert get_template("nps") is not None
    assert get_template("does-not-exist") is None


def test_template_definition_is_deep_copied():
    """template_definition returns an independent deep copy — mutating it must NOT bleed into
    the module-level catalog singleton (two surveys from one template must not share state)."""
    d1 = template_definition("nps")
    d2 = template_definition("nps")
    assert d1 is not d2
    assert d1["questions"] is not d2["questions"]
    # Mutate one copy; the catalog + a fresh copy stay clean.
    d1["questions"][0]["headline"]["default"] = "MUTATED"
    assert template_definition("nps")["questions"][0]["headline"]["default"] != "MUTATED"
    assert get_template("nps")["definition"]["questions"][0]["headline"]["default"] != "MUTATED"


def test_has_logic_flag():
    """has_logic reflects whether any question carries a logic branch (onboarding has one;
    plain NPS does not)."""
    assert has_logic(get_template("onboarding-feedback")["definition"]) is True
    assert has_logic(get_template("nps")["definition"]) is False


def test_at_least_one_template_has_logic():
    """The gallery's logic-branch indicator needs at least one template that exercises it."""
    assert any(has_logic(e["definition"]) for e in CATALOG)


# --------------------------------------------------------------------------- #
#  Tenant fixtures (mirror tests/test_surveys_admin.py)                        #
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
def org_ctx(transactional_db):
    """Org + owner + a single workspace/prod env. Yields (org, owner, env)."""
    owner = _mk_user("tpl_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="Template Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)
    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="Main WS", slug="ws-main")
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


def _u(path):
    return f"/{SLUG}{path}"


# --------------------------------------------------------------------------- #
#  2. Gallery page (GET /<slug>/surveys/new)                                   #
# --------------------------------------------------------------------------- #
def test_gallery_owner_200_renders_cards_and_filters(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys/new"))
    assert resp.status_code == 200
    html = resp.content.decode()
    # Title + Start-from-scratch card.
    assert "Create a new survey" in html
    assert "Start from scratch" in html
    # A template card appears (the NPS template's name).
    assert "Net Promoter Score (NPS)" in html
    # Filter chip labels for all three dimensions render.
    assert "Link Survey" in html and "Website Survey" in html and "App Survey" in html
    assert "E-Commerce" in html and "SaaS" in html
    assert "Product Manager" in html and "Customer Success" in html
    # The create-from-template POST form posts to the gallery URL ("Use template" button).
    assert f'<form method="post" action="/{SLUG}/surveys/new">' in html
    assert "Use template" in html


def test_gallery_filters_by_channel(client, org_ctx):
    """?channel=app narrows to app-channel templates; a link-only template is hidden."""
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys/new?channel=app"))
    assert resp.status_code == 200
    html = resp.content.decode()
    # App-channel template shown; a link-channel-only template hidden.
    assert "Churn / Cancellation" in html  # channel=app
    assert "Lead Qualification" not in html  # channel=link


def test_gallery_filters_by_industry(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys/new?industry=ecommerce"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Cart Abandonment" in html  # industry=ecommerce
    assert "Net Promoter Score (NPS)" not in html  # industry=saas


def test_gallery_filters_by_role(client, org_ctx):
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys/new?role=sales"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Lead Qualification" in html  # role=sales
    assert "Employee NPS (eNPS)" not in html  # role=people


def test_gallery_search_narrows(client, org_ctx):
    """?q= matches the template name/description; a non-matching template is hidden."""
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys/new?q=churn"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Churn / Cancellation" in html
    assert "Net Promoter Score (NPS)" not in html


def test_gallery_combined_filters(client, org_ctx):
    """Channel + industry + role compose (AND)."""
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys/new?channel=link&industry=saas&role=pm"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Net Promoter Score (NPS)" in html  # link + saas + pm
    assert "Cart Abandonment" not in html  # website + ecommerce + marketing


def test_gallery_reader_200_no_create_form(client, org_ctx):
    """A read-only member can SEE the gallery but the create forms are not rendered (cannot
    create anywhere). Mirrors the surveys-list can_create gating."""
    org, owner, env = org_ctx
    reader = _add_member(org, "tpl_reader@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.get(_u("/surveys/new"))
    assert resp.status_code == 200
    html = resp.content.decode()
    # The page (incl. the GET search form) renders, but NO create affordances: no
    # create-from-template POST form and no Start-from-scratch card (no writable env).
    assert f'<form method="post" action="/{SLUG}/surveys/new">' not in html
    assert "Use template" not in html
    assert "Start from scratch" not in html
    # The template names still render (the gallery is browsable read-only).
    assert "Net Promoter Score (NPS)" in html


def test_gallery_anonymous_redirects(client, org_ctx):
    org, owner, env = org_ctx
    resp = client.get(_u("/surveys/new"))
    assert resp.status_code in (301, 302)


def test_surveys_list_new_redirects_to_gallery(client, org_ctx):
    """The legacy ?new=1 entry point now lands on the gallery (the topbar CTA target)."""
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.get(_u("/surveys?new=1"))
    assert resp.status_code in (301, 302)
    assert resp["Location"].endswith("/surveys/new")


# --------------------------------------------------------------------------- #
#  3. Create-from-template (POST /<slug>/surveys/new)                          #
# --------------------------------------------------------------------------- #
def test_create_from_template_owner_redirects_to_builder(client, org_ctx):
    """Owner picks the NPS template → a DRAFT survey is created in the writable env with the
    template's questions, then the user is redirected to the builder host."""
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.post(_u("/surveys/new"), {"template": "nps"})
    assert resp.status_code == 302
    connection.set_tenant(org)
    created = Survey.objects.get(name="Net Promoter Score (NPS)")
    assert created.status == SurveyStatus.DRAFT
    assert created.environment_id == env.id
    # The template's questions were copied in (ids + types match the catalog definition).
    catalog_qs = get_template("nps")["definition"]["questions"]
    assert [q["id"] for q in created.questions] == [q["id"] for q in catalog_qs]
    assert created.questions[0]["type"] == "nps"
    # Redirect lands on the builder host for the new survey.
    assert resp["Location"] == _u(f"/surveys/{created.id}/edit")


def test_create_from_template_copies_logic(client, org_ctx):
    """A template WITH logic (onboarding) carries its logic branch into the created survey."""
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.post(_u("/surveys/new"), {"template": "onboarding-feedback"})
    assert resp.status_code == 302
    connection.set_tenant(org)
    created = Survey.objects.get(name="Onboarding Feedback")
    # The stuck-question's logic rule (jumpTo) survived the copy.
    stuck = next(q for q in created.questions if q["id"] == "onb_stuck")
    assert stuck["logic"], "logic branch was dropped"
    assert stuck["logic"][0]["action"]["type"] == "jumpTo"


def test_create_from_template_unknown_slug_shows_error(client, org_ctx):
    """An unknown template slug does NOT create a survey; the gallery re-renders with an error."""
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.post(_u("/surveys/new"), {"template": "no-such-template"})
    # Redirects back to the gallery with an error (no survey created).
    assert resp.status_code in (302, 200)
    connection.set_tenant(org)
    assert Survey.objects.count() == 0


def test_create_from_template_forbidden_for_reader(client, org_ctx):
    """A read-only member POSTing the create-from-template form → 403 (no write anywhere)."""
    org, owner, env = org_ctx
    reader = _add_member(org, "tpl_reader_post@example.com")
    _grant(org, reader, env.workspace_id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(_u("/surveys/new"), {"template": "nps"})
    assert resp.status_code == 403
    connection.set_tenant(org)
    assert Survey.objects.count() == 0


def test_create_from_template_anonymous_redirects(client, org_ctx):
    org, owner, env = org_ctx
    resp = client.post(_u("/surveys/new"), {"template": "nps"})
    assert resp.status_code in (301, 302)


# --------------------------------------------------------------------------- #
#  Start-from-scratch (blank path) still works                                 #
# --------------------------------------------------------------------------- #
def test_start_from_scratch_still_creates_blank_draft(client, org_ctx):
    """The blank path (POST /<slug>/surveys with a name) is unchanged: a blank DRAFT, then the
    builder. The gallery's 'Start from scratch' card posts here."""
    org, owner, env = org_ctx
    client.force_login(owner)
    resp = client.post(_u("/surveys"), {"name": "From Scratch Survey"})
    assert resp.status_code == 302
    connection.set_tenant(org)
    created = Survey.objects.get(name="From Scratch Survey")
    assert created.status == SurveyStatus.DRAFT
    assert created.questions == []
    assert resp["Location"] == _u(f"/surveys/{created.id}/edit")
