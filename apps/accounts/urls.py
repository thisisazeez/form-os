"""
PUBLIC-schema auth URLs (mounted in config/urls_public.py under ``api/v1/``).

Maps to contracts/openapi.yaml Auth tag:
  POST api/v1/auth/signup                  → SignupView
  POST api/v1/auth/verify-email            → VerifyEmailView
  POST api/v1/auth/login                   → LoginView
  POST api/v1/auth/logout                  → LogoutView
  POST api/v1/auth/password-reset          → PasswordResetView
  POST api/v1/auth/password-reset/confirm  → PasswordResetConfirmView
  GET  api/v1/auth/me                      → MeView   *** openapi path is /api/v1/me ***

BLOCKING openapi deviation (see SUMMARY + FOLLOWUPS.md): the contract puts `/api/v1/me`
at the top of the API namespace, but TenantSlugMiddleware (apps/common/middleware.py —
OUTSIDE this producer's territory) only whitelists `auth` as a public `/api/v1/<x>` segment;
every other first segment is treated as an org slug and 404s. So `me` is shipped under the
whitelisted `auth/` prefix until the middleware producer adds `me` to PUBLIC_CANDIDATES
(+ RESERVED_SLUGS). Moving it back is then a one-line edit here.
"""
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("auth/signup", views.SignupView.as_view(), name="signup"),
    path("auth/verify-email", views.VerifyEmailView.as_view(), name="verify-email"),
    path("auth/login", views.LoginView.as_view(), name="login"),
    path("auth/logout", views.LogoutView.as_view(), name="logout"),
    path("auth/password-reset", views.PasswordResetView.as_view(), name="password-reset"),
    path(
        "auth/password-reset/confirm",
        views.PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    # openapi: /api/v1/me — shipped at /api/v1/auth/me (middleware deviation above).
    path("auth/me", views.MeView.as_view(), name="me"),
]
