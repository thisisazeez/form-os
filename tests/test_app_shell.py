"""
Tests for the authenticated admin APP SHELL (Wave 0F foundation).

The shell (``templates/_layouts/app_shell.html``) is the chrome every signed-in admin page
extends. There is no production page on it yet (other agents are blocked on this review), so we
exercise it through a TEMPORARY smoke fixture:

  GET /{org_slug}/__shell_smoke → apps.common.views.app_shell_smoke
        (a @login_required page that extends the shell; DELETE both once a real admin page does)

Hit through the real TenantSlugMiddleware path (slug resolves + activates the org schema) with
Django's session ``Client`` + ``force_login`` — the shell page is a Django HTML view gated by
``@login_required`` (NOT DRF). Org creation provisions a Postgres schema (DDL) → ``transactional_db``
+ ``force_drop`` cleanup, mirroring tests/test_builder_host_page.py.

Coverage:
  - 302 → LOGIN_URL for an anonymous visitor (the auth gate is present).
  - 200 for a logged-in user, with the SHELL CONTRACT:
      * OpenSurvey wordmark present.
      * all seven sidebar nav items present (Home/Surveys/Contacts/Segments/Results/Team/Settings).
      * the active item carries the `is-active` sentinel (the smoke fixture marks Home).
      * brand TOKEN classes are used (bg-surface / bg-panel / text-brand / bg-brand).
      * NO `indigo` substring anywhere in the rendered HTML (brand-token purity).
  - base.html renders the Tailwind brand config (the inline `tailwind.config` with brand tokens),
    asserted via a base.html-rendered page (the public landing).
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client

from apps.organizations.models import Organization
from apps.organizations.services import create_organization

User = get_user_model()

SLUG = "shell-org"


# --------------------------------------------------------------------------- #
#  Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return Client()


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def shell_ctx(transactional_db):
    """Org + owner. Yields (org, owner)."""
    connection.set_schema_to_public()
    User.objects.filter(email="shell_owner@example.com").delete()
    owner = User.objects.create_user(
        email="shell_owner@example.com", password="x", name="Shell Owner", is_verified=True
    )
    _cleanup(SLUG)
    org = create_organization(name="Shell Org", slug=SLUG, owner=owner)
    yield org, owner
    _cleanup(SLUG)


def _smoke_url():
    return f"/{SLUG}/__shell_smoke"


# --------------------------------------------------------------------------- #
#  302 — login gate (anonymous)                                               #
# --------------------------------------------------------------------------- #
def test_shell_smoke_anonymous_redirects_to_login(client, shell_ctx):
    """Anonymous visitor → @login_required redirects to LOGIN_URL (the auth gate is present)."""
    resp = client.get(_smoke_url())
    assert resp.status_code in (301, 302)


# --------------------------------------------------------------------------- #
#  200 + SHELL CONTRACT                                                        #
# --------------------------------------------------------------------------- #
def test_shell_smoke_renders_for_logged_in_user(client, shell_ctx):
    org, owner = shell_ctx
    client.force_login(owner)
    resp = client.get(_smoke_url())
    assert resp.status_code == 200, resp.content
    html = resp.content.decode()

    # OpenSurvey wordmark — the brand half ("Survey") is split into its own span.
    assert "Open" in html
    assert 'text-brand">Survey</span>' in html


def test_shell_smoke_has_all_sidebar_nav_items(client, shell_ctx):
    org, owner = shell_ctx
    client.force_login(owner)
    html = client.get(_smoke_url()).content.decode()
    for label in ("Home", "Surveys", "Contacts", "Segments", "Results", "Team", "Settings"):
        assert f">{label}</span>" in html, f"missing sidebar nav item: {label}"
    # The sidebar links are tenant-scoped under the org slug.
    assert f'href="/{SLUG}/surveys"' in html
    assert f'href="/{SLUG}/contacts"' in html
    assert f'href="/{SLUG}/members"' in html  # Team → members


def test_shell_smoke_marks_active_nav_item(client, shell_ctx):
    """The smoke fixture marks Home active via the per-item `is-active` sentinel block."""
    org, owner = shell_ctx
    client.force_login(owner)
    html = client.get(_smoke_url()).content.decode()
    assert "is-active" in html
    # The active-styling CSS (token-driven, no hex) is shipped in the shell's head_extra.
    assert ".nav-item.is-active" in html


def test_shell_smoke_uses_brand_tokens(client, shell_ctx):
    org, owner = shell_ctx
    client.force_login(owner)
    html = client.get(_smoke_url()).content.decode()
    # Brand token classes from the shell + components.
    assert "bg-surface" in html
    assert "bg-panel" in html
    assert "text-brand" in html  # the brand wordmark accent + active nav
    assert "bg-brand" in html  # the "New survey" CTA / workspace badge
    # The top-bar primary action links to the surveys "new" entrypoint for now.
    assert f'href="/{SLUG}/surveys?new=1"' in html


def test_shell_smoke_has_no_indigo_leak(client, shell_ctx):
    """No `indigo` anywhere in the rendered shell — the old palette is fully removed."""
    org, owner = shell_ctx
    client.force_login(owner)
    html = client.get(_smoke_url()).content.decode()
    assert "indigo" not in html
    assert "slate-50" not in html


def test_shell_smoke_renders_components(client, shell_ctx):
    """The shell page composes the reusable _components partials (stat_card + empty_state)."""
    org, owner = shell_ctx
    client.force_login(owner)
    html = client.get(_smoke_url()).content.decode()
    assert "Active surveys" in html  # stat_card label
    assert "No surveys yet" in html  # empty_state title
    assert "App shell smoke" in html  # page_header title


# --------------------------------------------------------------------------- #
#  base.html — Tailwind brand config present on base-rendered pages           #
# --------------------------------------------------------------------------- #
def test_base_html_ships_tailwind_brand_config():
    """base.html carries the inline Tailwind brand config + Google Fonts + rebranded wordmark.

    Rendered DIRECTLY via the template engine (not through a view) so the assertion targets
    base.html's own chrome — independent of any page that overrides {% block title %}/copy. The
    config registers the brand TOKEN classes (brand/ink/muted/surface/panel/hairline + Poppins/
    Inter) so every admin/auth template that uses them resolves.
    """
    from django.template.loader import render_to_string

    html = render_to_string("base.html", {})
    assert "tailwind.config" in html
    # Brand tokens registered in the config.
    assert "brand:'#0d9488'" in html  # primary brand token → hex
    assert "'brand-hover':'#0f766e'" in html
    assert "surface:'#faf8f7'" in html
    assert "hairline:'rgba(34,43,39,0.10)'" in html
    assert "display:['Poppins','sans-serif']" in html
    assert "Poppins" in html  # display font family + Google Fonts link
    assert "Material+Symbols+Outlined" in html  # icon font linked
    # The CDN script precedes the config (config must extend an already-loaded Tailwind).
    assert html.index("cdn.tailwindcss.com") < html.index("tailwind.config")
    # …and the config precedes HTMX/Alpine (utilities must exist before markup is parsed).
    assert html.index("tailwind.config") < html.index("htmx.org")
    # The default body uses brand tokens, not the old palette.
    assert "bg-surface" in html
    assert "Open" in html and 'text-brand">Survey</span>' in html


# --------------------------------------------------------------------------- #
#  Organization switcher (sidebar) — lists the user's orgs                    #
# --------------------------------------------------------------------------- #
def test_org_switcher_shows_current_org_and_actions(client, shell_ctx):
    org, owner = shell_ctx
    client.force_login(owner)
    html = client.get(_smoke_url()).content.decode()
    assert "Your organizations" in html
    assert "Create organization" in html  # the create-org action in the switcher
    assert 'href="/auth/organizations/new"' in html
    assert 'href="/shell-org/"' in html  # current org links to its dashboard
    assert "bg-brand/5" in html  # current org is highlighted (switcher flags it)


def test_org_switcher_lists_all_user_orgs(client, shell_ctx):
    """A user in multiple orgs sees them all in the switcher (each linking to its dashboard)."""
    org, owner = shell_ctx
    slug2 = "shell-org-2"
    _cleanup(slug2)
    try:
        create_organization(name="Second Org", slug=slug2, owner=owner)
        client.force_login(owner)
        html = client.get(_smoke_url()).content.decode()
        assert 'href="/shell-org/"' in html
        assert 'href="/shell-org-2/"' in html
        assert "Second Org" in html
    finally:
        _cleanup(slug2)
