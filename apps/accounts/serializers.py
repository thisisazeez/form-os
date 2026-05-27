"""
DRF serializers for the auth API (apps.accounts.views).

These mirror the request/response shapes in contracts/openapi.yaml (Auth tag). Validation
of business rules (uniqueness, password strength, verification gate) lives in
apps.accounts.services — serializers here handle request *shape* + the response
``UserBrief`` projection. Password fields are write-only.
"""
from __future__ import annotations

from rest_framework import serializers


class UserBriefSerializer(serializers.Serializer):
    """openapi ``UserBrief`` = {id, email, name}. The only User shape returned by the API."""

    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    name = serializers.CharField(read_only=True, allow_blank=True)


class SignupSerializer(serializers.Serializer):
    """openapi ``SignupRequest`` = {email, password (minLength 8), name?}."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, style={"input_type": "password"})
    name = serializers.CharField(required=False, allow_blank=True, default="")


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8, style={"input_type": "password"})
