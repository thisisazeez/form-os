"""
Django forms backing the server-rendered (HTMX) org pages.

Thin shells over apps.organizations.services (the same layer the DRF API uses), so the
browser and API surfaces enforce identical rules.
"""
from __future__ import annotations

from django import forms

from .enums import OrgRole


class OrganizationCreateForm(forms.Form):
    name = forms.CharField(max_length=255, label="Organization name")
    slug = forms.SlugField(max_length=63, help_text="URL identifier (immutable after creation).")


class InvitationCreateForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(choices=OrgRole.choices, initial=OrgRole.MEMBER)
