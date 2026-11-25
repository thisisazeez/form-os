"""
create_demo_tenant — bootstrap a demo Organization + Domain + verified demo user.

Creating an Organization (TenantMixin) with auto_create_schema=True provisions the
Postgres schema AND runs the TENANT_APPS migrations into it (django-tenants). A single
shared Domain row points the org at the base domain (ADR-001 path-based routing — the
slug in the URL selects the tenant, not the domain).

The command also ensures the documented demo login works on a fresh deployment:

    email:    demo@onform.example
    password: DemoPass123!

That user is marked email-verified (login is gated on ``is_verified``), gets an ACTIVE
owner Membership in the demo org, and the org gets a default workspace so the dashboard
is immediately usable.

Usage:
    python manage.py create_demo_tenant
    python manage.py create_demo_tenant --slug demo --name "Demo Org" --domain localhost

Idempotent: re-running with the same slug is a no-op (won't duplicate).
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Domain, Membership, Organization

DEMO_EMAIL = "demo@onform.example"
DEMO_PASSWORD = "DemoPass123!"
DEMO_NAME = "Demo User"


class Command(BaseCommand):
    help = "Create the demo Organization, tenant schema, workspace and verified demo user."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="demo", help="Org slug (URL routing key).")
        parser.add_argument("--name", default="Demo Org", help="Org display name.")
        parser.add_argument(
            "--domain",
            default=getattr(settings, "BASE_DOMAIN", "localhost"),
            help="Shared base domain for the Domain row.",
        )
        parser.add_argument(
            "--email", default=DEMO_EMAIL, help="Demo user email (must be pre-verified)."
        )
        parser.add_argument(
            "--password", default=DEMO_PASSWORD, help="Demo user password."
        )

    def handle(self, *args, **options):
        slug = options["slug"]
        name = options["name"]
        domain_name = options["domain"]
        email = options["email"]
        password = options["password"]

        user = self._ensure_demo_user(email, password)

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

        # Ensure the demo user owns the demo org so post-login redirects land on /demo/.
        membership, m_created = Membership.objects.get_or_create(
            user=user,
            organization=org,
            defaults={"role": OrgRole.OWNER, "status": MembershipStatus.ACTIVE},
        )
        if m_created:
            self.stdout.write(
                self.style.SUCCESS(f"Granted '{email}' owner membership in '{slug}'.")
            )
        else:
            self.stdout.write(f"'{email}' already has a membership in '{slug}'.")

        # Surveys live under a workspace; an org with none is a dead end. Create the default
        # workspace inside the tenant schema if this is a fresh tenant (or an old one missing it).
        self._ensure_default_workspace(org, user)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDemo ready. Login: {email} / {password}\n"
                f"Workspace: http://{domain_name}:8000/{slug}/"
            )
        )

    @staticmethod
    def _ensure_demo_user(email: str, password: str):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=User.objects.normalize_email(email),
            defaults={
                "name": DEMO_NAME,
                "is_verified": True,
                "is_active": True,
            },
        )
        changed = []
        if not user.is_verified:
            user.is_verified = True
            changed.append("is_verified")
        if not user.is_active:
            user.is_active = True
            changed.append("is_active")
        if not user.name:
            user.name = DEMO_NAME
            changed.append("name")
        # Always set the documented demo password so the published credentials work after a
        # fresh deploy (and after redeploys against a persisted free Postgres database).
        user.set_password(password)
        changed.append("password")
        if changed:
            user.save(update_fields=list(dict.fromkeys(changed)) + ["updated_at"])
        return user

    @staticmethod
    def _ensure_default_workspace(org, user) -> None:
        from django_tenants.utils import tenant_context

        from apps.workspaces.models import Workspace
        from apps.workspaces.services import create_workspace

        with tenant_context(org):
            if Workspace.objects.exists():
                return
            create_workspace(
                name="Default workspace",
                slug=None,
                created_by_user_id=user.id,
            )
