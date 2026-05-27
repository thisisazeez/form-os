"""
HTMX (server-rendered) auth page URLs — public schema (config/urls_public.py).

These are the browser pages, distinct from the JSON API in apps.accounts.urls. They sit at
top-level public paths (no /api/ prefix):
  /signup  /login  /logout  /verify-email  /reset-password

The verification + password-reset EMAIL links point here (apps.accounts.emails builds
``/verify-email?token=…`` and ``/reset-password?token=…``).
"""
from django.urls import path

from . import views_htmx

app_name = "accounts_htmx"

urlpatterns = [
    path("signup", views_htmx.signup_page, name="signup"),
    path("login", views_htmx.login_page, name="login"),
    path("logout", views_htmx.logout_page, name="logout"),
    path("verify-email", views_htmx.verify_email_page, name="verify-email"),
    path("reset-password", views_htmx.password_reset_page, name="password-reset"),
]
