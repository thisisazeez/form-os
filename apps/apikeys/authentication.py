"""
DRF API-key authentication — the ``x-api-key`` header auth class (rbac.md §8.1, models.md §2.15).

Mounted in ``DEFAULT_AUTHENTICATION_CLASSES`` ALONGSIDE SessionAuthentication: browser/HTMX
admin keeps using the session cookie; programmatic API clients present a raw key in the
``x-api-key`` request header. Either path produces an authenticated ``request.user`` against
which the existing ``can()`` RBAC runs unchanged.

FLOW (the tenant schema is ALREADY active here — TenantSlugMiddleware ran before DRF auth):
  1. Read the ``x-api-key`` header. Absent → return None (let other authenticators / the
     IsAuthenticated default decide; a session request is unaffected).
  2. Hash the presented value and look it up by ``hashed_key`` in the ACTIVE tenant schema.
     No match → ``AuthenticationFailed`` (401). Scoping is automatic: a key only exists in its
     own org's schema, so a key issued for org A cannot authenticate a request routed to org B.
  3. Reject a revoked key (``revoked_at`` set) → 401.
  4. Resolve the creating User from the PUBLIC schema (cross-schema, ADR-002) and return
     ``(user, api_key)``. Downstream ``can()`` evaluates that user's Membership for the routed
     org — so a key whose creator lost membership is correctly denied at the authz layer.
  5. Best-effort bump ``last_used_at`` (never fails the request).

``authenticate_header`` returns a scheme string so DRF answers 401 (not 403) for a
missing/invalid key on an otherwise-IsAuthenticated endpoint.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework import authentication, exceptions

from .models import ApiKey
from .services import hash_key

# The request header API clients present. Django exposes it on request.META as HTTP_X_API_KEY,
# but DRF's request.headers (a case-insensitive mapping) lets us read it by its wire name.
API_KEY_HEADER = "x-api-key"

User = get_user_model()


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate a request by a raw API key in the ``x-api-key`` header."""

    def authenticate(self, request):
        raw_key = request.headers.get(API_KEY_HEADER)
        if not raw_key:
            # No key presented → defer (session auth / default IsAuthenticated handle it).
            return None

        try:
            api_key = ApiKey.objects.select_related("environment").get(
                hashed_key=hash_key(raw_key)
            )
        except ApiKey.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid API key.") from None

        if api_key.revoked_at is not None:
            raise exceptions.AuthenticationFailed("API key has been revoked.")

        user = self._resolve_user(api_key)
        if user is None:
            # Key with no resolvable creator (e.g. user deleted) → treat as invalid.
            raise exceptions.AuthenticationFailed("Invalid API key.")

        self._touch_last_used(api_key)
        return (user, api_key)

    def authenticate_header(self, request) -> str:
        # Presence of a WWW-Authenticate scheme makes DRF return 401 (not 403) for
        # unauthenticated requests to IsAuthenticated endpoints.
        return "ApiKey"

    # --- helpers ---------------------------------------------------------- #
    @staticmethod
    def _resolve_user(api_key: ApiKey):
        """Resolve the key's creator from the PUBLIC schema (User is public — ADR-002)."""
        if api_key.created_by_user_id is None:
            return None
        with schema_context(get_public_schema_name()):
            return User.objects.filter(pk=api_key.created_by_user_id).first()

    @staticmethod
    def _touch_last_used(api_key: ApiKey) -> None:
        """Best-effort ``last_used_at`` bump. Must never break the authenticated request."""
        try:
            api_key.last_used_at = timezone.now()
            api_key.save(update_fields=["last_used_at", "updated_at"])
        except Exception:  # noqa: BLE001 — telemetry only; never fail auth on this.
            pass
