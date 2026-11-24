"""
Server-rendered (HTMX) auth pages — the browser counterpart of the JSON API.

PLAN §8: the admin shell is HTMX + Alpine + Tailwind. These views render full pages
(GET) and process form posts (POST), delegating ALL business logic to
apps.accounts.services — the identical service layer the DRF API uses, so the two
surfaces never diverge.

Pages (public schema; mounted in config/urls_public.py):
  /signup            signup form → service.signup → "check your email" confirmation
  /login             login form  → service.authenticate_for_login → session login → redirect
  /logout            POST → django logout → landing
  /verify-email      GET ?token=… → service.verify_email → success/failure page
  /reset-password    request form (POST email) + confirm form (GET ?token=…, POST new pw)

These render with Django's CSRF token in the form; DRF's CSRF concerns don't apply here
(plain Django views + SessionMiddleware). htmx posts include the token via hx-headers or
the rendered {% csrf_token %} input.
"""
from __future__ import annotations

from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from . import services
from .forms import (
    LoginForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    SignupForm,
)


@require_http_methods(["GET", "POST"])
def signup_page(request):
    # Already signed in? No reason to show signup — bounce into the app.
    if request.user.is_authenticated:
        return redirect(_post_login_redirect(request, request.user))
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            try:
                services.signup(
                    email=form.cleaned_data["email"],
                    password=form.cleaned_data["password"],
                    name=form.cleaned_data.get("name", ""),
                )
            except services.EmailAlreadyRegistered:
                form.add_error("email", "A user with this email already exists.")
            except ValidationError as exc:
                _attach_service_errors(form, exc, default_field="password")
            else:
                return render(
                    request,
                    "accounts/signup_done.html",
                    {"email": form.cleaned_data["email"]},
                )
    else:
        form = SignupForm()
    return render(request, "accounts/signup.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_page(request):
    # Already signed in? Don't show the login form — bounce into the app (or honor a safe ?next=).
    if request.user.is_authenticated:
        return redirect(_post_login_redirect(request, request.user))
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            try:
                user = services.authenticate_for_login(
                    email=form.cleaned_data["email"],
                    password=form.cleaned_data["password"],
                )
            except ValidationError:
                form.add_error(None, "Invalid credentials or unverified email.")
            else:
                django_login(request, user)
                # Auto-join any orgs this (now email-verified) user was invited to, so an
                # invited user who just signed up lands IN the org rather than on the create-org
                # page. The signup→verify→login chain otherwise drops the invite token.
                from apps.organizations.services import accept_pending_invitations

                accept_pending_invitations(user)
                return redirect(_post_login_redirect(request, user))
    else:
        form = LoginForm()
    return render(request, "accounts/login.html", {"form": form})


@require_http_methods(["POST"])
def logout_page(request):
    django_logout(request)
    return redirect(reverse("landing"))


@require_http_methods(["GET"])
def verify_email_page(request):
    """GET /verify-email?token=… — confirm the email and show the result."""
    token = request.GET.get("token", "")
    ok = True
    try:
        services.verify_email(token=token)
    except ValidationError:
        ok = False
    return render(request, "accounts/verify_email.html", {"ok": ok})


@require_http_methods(["GET", "POST"])
def password_reset_page(request):
    """The reset page serves two phases:

    - No ``token`` query param → the "request a reset link" email form (always reports
      success to avoid enumeration).
    - ``token`` present → the "set a new password" form (GET shows it, POST applies it).
    """
    token = request.GET.get("token") or request.POST.get("token")

    if token:
        return _password_reset_confirm(request, token)
    return _password_reset_request(request)


def _password_reset_request(request):
    sent = False
    if request.method == "POST":
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            services.request_password_reset(email=form.cleaned_data["email"])
            sent = True  # always — no account enumeration
    else:
        form = PasswordResetRequestForm()
    return render(
        request, "accounts/password_reset_request.html", {"form": form, "sent": sent}
    )


def _password_reset_confirm(request, token):
    done = False
    if request.method == "POST":
        form = PasswordResetConfirmForm(request.POST)
        if form.is_valid():
            try:
                services.confirm_password_reset(
                    token=form.cleaned_data["token"],
                    password=form.cleaned_data["password"],
                )
            except ValidationError as exc:
                _attach_service_errors(form, exc, default_field="password")
            else:
                done = True
    else:
        form = PasswordResetConfirmForm(initial={"token": token})
    return render(
        request, "accounts/password_reset_confirm.html", {"form": form, "done": done}
    )


def _safe_next(request) -> str | None:
    """Return the ``?next=`` redirect target only if it's a safe same-host relative URL.

    Guards against open-redirect: an attacker-supplied ``next=https://evil.com`` is rejected.
    """
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return None


def _post_login_redirect(request, user) -> str:
    """Where to send a user after a successful login.

    Priority: a safe ``?next=`` (e.g. the @login_required page they were heading to) → the
    user's first active-org dashboard (``/<slug>/``) → the create-org page if they have no
    org yet. Never the public marketing homepage (``/``): it is now a static landing page,
    so returning there after login would strand the user. The old app dispatcher is at
    ``/app`` (apps.common.views.landing).
    """
    nxt = _safe_next(request)
    if nxt:
        return nxt
    # Local import avoids a module-load cycle (organizations.services ↔ accounts).
    from apps.organizations.services import first_active_org

    org = first_active_org(user)
    if org is not None:
        return f"/{org.slug}/"
    return reverse("org_public_htmx:org-create")


def _attach_service_errors(form, exc: ValidationError, *, default_field: str) -> None:
    """Map a service ValidationError onto form fields (or a non-field error)."""
    if hasattr(exc, "message_dict"):
        for field, messages in exc.message_dict.items():
            target = field if field in form.fields else None
            for msg in messages:
                form.add_error(target, msg)
    else:
        target = default_field if default_field in form.fields else None
        for msg in exc.messages:
            form.add_error(target, msg)
