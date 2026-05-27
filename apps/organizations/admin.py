"""Admin registrations for public-schema org models."""
from django.contrib import admin

from .models import Domain, Invitation, Membership, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "schema_name", "created_at")
    search_fields = ("name", "slug", "schema_name")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary")
    search_fields = ("domain",)
    readonly_fields = ("id",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "status", "created_at")
    list_filter = ("role", "status")
    search_fields = ("user__email", "organization__slug")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "organization", "role", "status", "expires_at", "created_at")
    list_filter = ("role", "status")
    search_fields = ("email", "organization__slug")
    readonly_fields = ("id", "token", "created_at", "updated_at")
