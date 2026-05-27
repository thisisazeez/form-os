"""
create_demo_tenant — bootstrap a demo Organization + Domain for local dev / smoke tests.

Creating an Organization (TenantMixin) with auto_create_schema=True provisions the
Postgres schema AND runs the TENANT_APPS migrations into it (django-tenants). A single
shared Domain row points the org at the base domain (ADR-001 path-based routing — the
slug in the URL selects the tenant, not the domain).

Usage:
    python manage.py create_demo_tenant
    python manage.py create_demo_tenant --slug acme --name "Acme Inc" --domain localhost

Idempotent: re-running with the same slug is a no-op (won't duplicate).
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.organizations.models import Domain, Organization


class Command(BaseCommand):
    help = "Create a demo Organization (tenant) + Domain for local dev / smoke tests."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="demo", help="Org slug (URL routing key).")
        parser.add_argument("--name", default="Demo Org", help="Org display name.")
        parser.add_argument(
            "--domain",
            default=getattr(settings, "BASE_DOMAIN", "localhost"),
            help="Shared base domain for the Domain row.",
        )

    def handle(self, *args, **options):
        slug = options["slug"]
        name = options["name"]
        domain_name = options["domain"]

        org, created = Organization.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "schema_name": slug},
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created tenant '{slug}' (schema '{org.schema_name}') + ran tenant migrations."
                )
            )
        else:
            self.stdout.write(f"Tenant '{slug}' already exists (schema '{org.schema_name}').")

        # Domain row (DomainMixin). In path-based mode this points the org at the base domain;
        # the URL slug does the actual tenant selection (ADR-001).
        domain, d_created = Domain.objects.get_or_create(
            tenant=org,
            domain=domain_name,
            defaults={"is_primary": True},
        )
        if d_created:
            self.stdout.write(self.style.SUCCESS(f"Created domain '{domain_name}' for '{slug}'."))
        else:
            self.stdout.write(f"Domain '{domain_name}' already linked to '{slug}'.")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDemo tenant ready. Visit:  http://{domain_name}:8000/{slug}/"
            )
        )
