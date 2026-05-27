"""Minimal admin registration for the custom User (public schema)."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Email-login user admin. Field layout adapted for the no-username custom model."""

    ordering = ("email",)
    list_display = ("email", "name", "is_verified", "is_active", "is_staff")
    list_filter = ("is_verified", "is_active", "is_staff", "is_superuser")
    search_fields = ("email", "name")
    readonly_fields = ("id", "created_at", "updated_at", "last_login")

    fieldsets = (
        (None, {"fields": ("id", "email", "password")}),
        ("Profile", {"fields": ("name",)}),
        ("Status", {"fields": ("is_verified", "is_active")}),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Timestamps", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "password1", "password2", "is_staff", "is_superuser"),
            },
        ),
    )
