"""
DRF API views for the public-schema auth flows (contracts/openapi.yaml — Auth tag).

These live on the PUBLIC schema urlconf (config/urls_public.py) and are mounted under
``/api/v1/auth/...`` + ``/api/v1/me``. All but logout/me are ``AllowAny`` (the project
default permission is ``IsAuthenticated`` — see settings; we override per-view).

Business logic is delegated to apps.accounts.services; ``ValidationError`` from there is
translated to the HTTP code the contract specifies (signup 409 on dup email; 400 on bad
token / weak password; 401 on bad login / unverified).

Session auth: login() uses Django's ``login()`` to set the ``sessionid`` cookie (openapi
``sessionAuth``); logout() clears it.
"""
from __future__ import annotations

from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Membership

from . import services
from .serializers import (
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    SignupSerializer,
    UserBriefSerializer,
    VerifyEmailSerializer,
)


def _validation_errors(exc: DjangoValidationError) -> dict:
    """Render a Django ValidationError as a DRF-style ``{field: [msgs]}`` / ``{detail}``."""
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"detail": exc.messages[0] if exc.messages else "Invalid request."}


class SignupView(APIView):
    """POST /api/v1/auth/signup → create user (unverified) + send verification email."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = services.signup(**serializer.validated_data)
        except services.EmailAlreadyRegistered as exc:
            # openapi: 409 Email already registered.
            return Response(_validation_errors(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            # openapi: 400 ValidationError (e.g. weak password).
            return Response(_validation_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(UserBriefSerializer(user).data, status=status.HTTP_201_CREATED)


class VerifyEmailView(APIView):
    """POST /api/v1/auth/verify-email → set is_verified=true."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.verify_email(token=serializer.validated_data["token"])
        except DjangoValidationError as exc:
            return Response(_validation_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Email verified."}, status=status.HTTP_200_OK)


class LoginView(APIView):
    """POST /api/v1/auth/login → session login (requires verified). Sets sessionid cookie."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = services.authenticate_for_login(**serializer.validated_data)
        except DjangoValidationError:
            # openapi: 401 invalid credentials or unverified email (uniform message).
            return Response(
                {"detail": "Invalid credentials or unverified email."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        django_login(request, user)
        return Response(UserBriefSerializer(user).data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """POST /api/v1/auth/logout → invalidate the session. 204."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        django_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetView(APIView):
    """POST /api/v1/auth/password-reset → always 202 (no account enumeration)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_password_reset(email=serializer.validated_data["email"])
        # Always 202 whether or not the email exists.
        return Response(
            {"detail": "If the email exists, a reset link was sent."},
            status=status.HTTP_202_ACCEPTED,
        )


class PasswordResetConfirmView(APIView):
    """POST /api/v1/auth/password-reset/confirm → set new password from token."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.confirm_password_reset(**serializer.validated_data)
        except DjangoValidationError as exc:
            return Response(_validation_errors(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Password updated."}, status=status.HTTP_200_OK)


class MeView(APIView):
    """GET /api/v1/me → current user + their org memberships (drives the org switcher).

    Memberships are public-schema rows for the authenticated user across ALL their orgs
    (openapi ``MembershipBrief``: org id/slug/name + role + status). This is schema-agnostic
    — it deliberately does NOT depend on a routed tenant.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        memberships = (
            Membership.objects.filter(user=request.user)
            .select_related("organization")
            .order_by("organization__name")
        )
        membership_briefs = [
            {
                "organization_id": str(m.organization_id),
                "organization_slug": m.organization.slug,
                "organization_name": m.organization.name,
                "role": m.role,
                "status": m.status,
            }
            for m in memberships
        ]
        return Response(
            {
                "user": UserBriefSerializer(request.user).data,
                "memberships": membership_briefs,
            },
            status=status.HTTP_200_OK,
        )
