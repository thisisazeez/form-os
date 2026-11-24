"""
pytest fixtures for the onForm Phase-0 smoke suite.

django-tenants needs Postgres + a schema-aware migration path: pytest-django's default
`django_db_setup` runs `migrate`, but django-tenants requires `migrate_schemas` (shared
schema first, then per-tenant). We override `django_db_setup` to build the test DB with
`migrate_schemas`, and expose a `demo_tenant` fixture that creates an Organization
(auto-provisions its schema + runs tenant migrations) plus a Domain.

Reference: django-tenants docs "Running tests" + REVIEW.md §2.5 (migrations apply on a
fresh DB, shared then tenant; ≥1 test tenant).
"""
import pytest
from django.core.management import call_command
from django.db import connection


@pytest.fixture(scope="session")
def django_db_setup(django_db_blocker):
    """Build the test database using django-tenants' migrate_schemas.

    pytest-django names the test DB `test_<NAME>`. We create it, then run shared and
    tenant migrations the django-tenants way. Teardown drops the test DB.
    """
    from django.test.utils import setup_databases, teardown_databases

    with django_db_blocker.unblock():
        # Create the test database(s) without running the default `migrate`.
        old_config = setup_databases(
            verbosity=0,
            interactive=False,
            aliases={"default"},
            # run_syncdb False; we drive migrations explicitly below.
        )
        # Ensure the public (shared) schema is fully migrated, then any existing tenants.
        # On a fresh test DB there are no tenants yet; tenant fixtures create them on demand,
        # which runs TENANT_APPS migrations into the new schema automatically.
        call_command("migrate_schemas", "--shared", verbosity=0, interactive=False)

    yield

    with django_db_blocker.unblock():
        teardown_databases(old_config, verbosity=0)


@pytest.fixture
def demo_tenant(transactional_db):
    """Create a demo Organization (tenant) + Domain; activate its schema for the test.

    Creating the Organization auto-provisions its Postgres schema and runs the
    TENANT_APPS migrations into it (django-tenants). Yields the Organization; restores
    the public schema and drops the tenant schema on teardown.

    Uses `transactional_db` (NOT the plain `db` fixture): tenant schema CREATE/DROP are
    DDL operations that cannot run inside pytest-django's per-test atomic transaction —
    rolling such DDL back triggers "cannot DROP TABLE ... pending trigger events". The
    transactional fixture commits, so schema provisioning + force_drop work correctly.
    """
    from apps.organizations.models import Domain, Organization

    connection.set_schema_to_public()
    # Clean any leftover from a previously-aborted run (transactional DB isn't auto-rolled).
    Organization.objects.filter(slug="demo").delete()

    org = Organization(name="Demo Org", slug="demo", schema_name="demo")
    org.save()  # auto_create_schema=True → provisions schema + runs tenant migrations
    Domain.objects.create(tenant=org, domain="localhost", is_primary=True)

    yield org

    connection.set_schema_to_public()
    org.delete(force_drop=True)
