"""
Django forms backing the server-rendered (HTMX) auth pages.

These are thin: they collect + shape input, then the HTMX views call into
apps.accounts.services (the same service layer the DRF API uses), so behavior can't drift
between the JSON API and the browser surface. Business validation (uniqueness, password
strength, the verified-gate) happens in the service and is surfaced back as form errors.
"""
from __future__ import annotations

from django import forms


class SignupForm(forms.Form):
    name = forms.CharField(max_length=255, required=False)
    email = forms.EmailField()
    password = forms.CharField(min_length=8, widget=forms.PasswordInput)


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField()


class PasswordResetConfirmForm(forms.Form):
    # Token comes from the link (?token=...) — carried as a hidden field across submit.
    token = forms.CharField(widget=forms.HiddenInput)
    password = forms.CharField(min_length=8, widget=forms.PasswordInput, label="New password")
