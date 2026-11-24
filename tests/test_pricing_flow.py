"""Pricing flow smoke tests.

The only published plan is self-hosted free — no hosted tiers and no payment gateway — so
"the pricing flow" means the end-to-end browser journey:

    landing → signup → email verification → login → app → billing page

These tests drive that journey through the REAL URLconf + TenantSlugMiddleware so a broken
CTA, route or plan page fails loudly.
"""
import re
from urllib.parse import unquote

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test import Client

from apps.organizations.models import Organization
from apps.organizations.services import create_organization

User = get_user_model()


@pytest.fixture
def billing_org(transactional_db):
    """A provisioned org + owner for the in-app billing page (mirrors other tenant tests)."""
    connection.set_schema_to_public()
    user = User.objects.create_user(
        email="pricing-owner@example.com", password="Pr1cingTest!", is_verified=True
    )
    for org in Organization.objects.filter(slug="pricing-org"):
        org.delete(force_drop=True)
    org = create_organization(name="Pricing Org", slug="pricing-org", owner=user)
    yield org, user
    connection.set_schema_to_public()
    org.delete(force_drop=True)


@pytest.mark.django_db
def test_landing_shows_only_the_self_hosted_plan():
    html = Client().get("/").content.decode()

    # One plan, priced in naira — no dollar or hosted-tier leftovers.
    assert "₦0" in html
    assert "NGN" in html
    assert "Self-hosted Free" in html
    assert "$0" not in html and "$49" not in html
    assert "₦25,000" not in html
    for hosted_tier in ("Cloud", "Enterprise", "Start free trial", "Contact sales"):
        assert hosted_tier not in html

    # The pricing card and the header/hero CTAs resolve to real destinations.
    assert 'https://github.com/onform/onform#quick-start' in html
    assert 'href="/auth/signup"' in html
    assert html.count('href="#"') == 0


@pytest.mark.django_db
def test_pricing_cta_signup_to_login_journey():
    """Clicking 'Start free' must reach signup and complete verification + login."""
    mail.outbox.clear()
    client = Client()

    landing = client.get("/").content.decode()
    signup_url = re.search(r'href="(/auth/signup[^"]*)"', landing).group(1)
    assert client.get(signup_url).status_code == 200

    resp = client.post(
        signup_url,
        {
            "name": "Pricing Tester",
            "email": "pricing-tester@example.com",
            "password": "sup3rsecretpw",
        },
    )
    assert resp.status_code == 200
    assert b"verification link" in resp.content.lower()

    token = unquote(re.search(r"[?&]token=(\S+)", mail.outbox[-1].body).group(1))
    assert client.get(f"/auth/verify-email?token={token}").status_code == 200
    assert User.objects.get(email="pricing-tester@example.com").is_verified

    # Unverified login was blocked earlier; now it lands in the app bootstrap.
    resp = client.post(
        "/auth/login",
        {"email": "pricing-tester@example.com", "password": "sup3rsecretpw"},
    )
    assert resp.status_code == 302
    assert resp["Location"] == "/auth/organizations/new"


def test_in_app_billing_page_shows_naira(billing_org):
    org, owner = billing_org
    client = Client()
    client.force_login(owner)
    resp = client.get(f"/{org.slug}/settings/billing")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Current plan" in html
    assert "₦0" in html
    assert "$0" not in html
