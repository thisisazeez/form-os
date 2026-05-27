"""
e2e/seed.py — idempotent seed for the Playwright parity E2E harness.

Run against the DEDICATED ``parity_e2e`` database (NEVER the live ``opensurvey_demo``):

    DATABASE_URL=postgres://opensurvey:opensurvey@localhost:5432/parity_e2e \
    DJANGO_SETTINGS_MODULE=config.settings.dev \
    .venv/bin/python manage.py shell -c "import e2e.seed as s; s.run()"

Builds, reusing the production service layer so the data matches what the app expects:
  - a verified OWNER ``User`` (public schema)
  - a tenant ``Organization`` (slug ``globex``) via ``create_organization`` — which
    auto-provisions the org's Postgres schema, runs tenant migrations, makes the owner
    ``Membership``, and creates a default ``Workspace`` + production/development
    ``Environment`` rows
  - one PUBLISHED ``Survey`` in the production environment with two question types
    (openText + rating) and a distinct ending screen.

Idempotent: deletes any prior ``globex`` org + seed user before recreating (mirrors the
pattern in tests/test_public_runtime_api.py). Re-running is a clean rebuild.

``run()`` returns a dict of the created ids — global-setup reads it to mint the session and
to point specs at the right survey. No passwords are read by the harness (auth is by minting
a Django session server-side; see e2e/global-setup.ts), but ``create_user`` needs *a*
password to construct a valid user — it is an internal, non-exported throwaway value, never
used to log in.
"""
from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.db import connection
from django_tenants.utils import schema_context, get_public_schema_name

# These are the stable identities the harness keys on. Other feature agents can import them.
ORG_SLUG = "globex"
ORG_NAME = "Globex Corporation"
OWNER_EMAIL = "e2e-owner@globex.test"
OWNER_NAME = "E2E Owner"
WORKSPACE_SLUG = "default-workspace"  # create_organization names it "Default workspace"

# A minimal but schema-valid question set (mirrors tests/test_public_runtime_api.py — proven
# to pass the server-side jsonschema + logic validation that publishing enforces). Two question
# types so the builder/runtime specs exercise more than one renderer.
QUESTIONS = [
    {
        "id": "q1",
        "type": "openText",
        "headline": {"default": "What is your name?"},
        "required": True,
        "config": {"inputType": "short"},
    },
    {
        "id": "q2",
        "type": "rating",
        "headline": {"default": "How would you rate us?"},
        "required": False,
        "config": {"range": 5, "scale": "number"},
    },
]
LANGUAGES = [{"code": "default", "label": "Default", "default": True}]
# A DISTINCT ending headline so the runtime spec asserts on stable text (NOT the generic
# "Thanks for your feedback!" fallback the runtime shows when no ending is configured).
ENDING_HEADLINE = "All done — thank you for your time!"
ENDINGS = [
    {"id": "end1", "type": "endScreen", "headline": {"default": ENDING_HEADLINE}}
]

SURVEY_NAME = "Customer Satisfaction Survey"


def run() -> dict:
    """(Re)build the seed. Returns the ids the harness needs as a plain dict."""
    User = get_user_model()

    # --- 1) Owner user (public schema). Idempotent: drop any prior seed user first. ---
    connection.set_schema_to_public()
    User.objects.filter(email=OWNER_EMAIL).delete()
    owner = User.objects.create_user(
        email=OWNER_EMAIL,
        # Throwaway: the harness authenticates by minting a session, never by password.
        password="seed-only-not-used-for-login",
        is_verified=True,
    )
    owner.name = OWNER_NAME
    owner.save(update_fields=["name"])

    # --- 2) Org (+ tenant schema, owner membership, default workspace + envs). ---
    # Imported lazily so this module imports cleanly even before Django is fully set up.
    from apps.organizations.models import Organization
    from apps.organizations.services import create_organization

    # Idempotent: force-drop any prior org with this slug (DDL — drops its schema too).
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=ORG_SLUG):
        org.delete(force_drop=True)

    org = create_organization(name=ORG_NAME, slug=ORG_SLUG, owner=owner)

    # --- 3) One PUBLISHED survey in the org's production environment. ---
    from apps.surveys.enums import SurveyStatus
    from apps.surveys.models import Survey
    from apps.workspaces.enums import EnvType
    from apps.workspaces.models import Environment

    with schema_context(org.schema_name):
        prod_env = (
            Environment.objects.filter(type=EnvType.PRODUCTION)
            .order_by("created_at")
            .first()
        )
        if prod_env is None:
            # create_organization always makes one, but be defensive.
            raise RuntimeError("No production environment found in the seeded org.")

        survey = Survey.objects.create(
            environment=prod_env,
            name=SURVEY_NAME,
            status=SurveyStatus.PUBLISHED,
            questions=QUESTIONS,
            languages=LANGUAGES,
            styling={"brandColor": "#0d9488"},
            welcome_card=None,  # no welcome card → runtime jumps straight to the first question
            endings=ENDINGS,
            created_by_user_id=owner.id,
        )
        survey_id = str(survey.id)
        env_id = str(prod_env.id)
        workspace_id = str(prod_env.workspace_id)

    connection.set_schema_to_public()

    result = {
        "org_slug": ORG_SLUG,
        "org_id": str(org.id),
        "schema_name": org.schema_name,
        "owner_email": OWNER_EMAIL,
        "owner_id": str(owner.id),
        "workspace_id": workspace_id,
        "environment_id": env_id,
        "survey_id": survey_id,
        "survey_name": SURVEY_NAME,
        "ending_headline": ENDING_HEADLINE,
    }
    # Emit a machine-readable marker line so callers running `manage.py shell -c` can scrape it.
    print("E2E_SEED_RESULT=" + json.dumps(result))
    return result


if __name__ == "__main__":  # pragma: no cover - convenience for `python -m`
    import django

    django.setup()
    run()
