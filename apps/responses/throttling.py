"""
Rate limiting for the PUBLIC survey runtime (PLAN §7/§16; openapi ``x-rate-limit``).

ADR / REVIEW.md §3.5: every public write endpoint is rate-limited **per IP + per survey
(or per response)**. The openapi ``x-rate-limit`` hints give the target buckets:

  - GET public survey definition ...... per-IP, per-survey   (~60/min)
  - POST create response .............. per-IP, per-survey   (strict; ~20/min)
  - PATCH response .................... per-IP, per-response  (~120/min)
  - POST finalize ..................... per-IP, per-response
  - POST display ...................... per-IP, per-survey

We subclass DRF's ``SimpleRateThrottle`` with a HARDCODED ``rate`` and a custom
``get_cache_key`` that scopes the bucket to ``IP + {survey_id|response_id}`` (read from the
resolved view kwargs). Hardcoding the rate (rather than relying on
``DEFAULT_THROTTLE_RATES`` in settings) keeps this self-contained — no settings change —
and makes the per-IP+per-survey scoping explicit and reviewable in one place.

The cache backend is whatever ``CACHES['default']`` is (locmem in dev/CI); the throttle
state is a short sliding window, so locmem is fine for tests (make N+1 calls → 429).
"""
from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class _PublicBaseThrottle(SimpleRateThrottle):
    """Base for the public-runtime throttles.

    Subclasses set ``scope`` (a label), ``rate`` (hardcoded "N/duration"), and
    ``key_kwarg`` (the view kwarg whose value scopes the bucket alongside the IP).
    """

    # Set by subclasses.
    rate: str = ""
    key_kwarg: str = ""

    def get_rate(self) -> str:
        # Bypass DEFAULT_THROTTLE_RATES lookup; the rate is hardcoded on the class.
        return self.rate

    def get_cache_key(self, request, view):
        # Identify the client by IP (DRF's ident handles X-Forwarded-For per settings).
        ident = self.get_ident(request)
        # Scope by the survey/response in the path so one survey's traffic can't exhaust
        # another's bucket (per-IP + per-survey/response, not a single global per-IP bucket).
        scope_value = view.kwargs.get(self.key_kwarg, "") if hasattr(view, "kwargs") else ""
        return f"throttle_public_{self.scope}_{scope_value}_{ident}"


class PublicSurveyReadThrottle(_PublicBaseThrottle):
    """GET public survey definition — per IP, per survey (~60/min)."""

    scope = "public_survey_read"
    rate = "60/min"
    key_kwarg = "survey_id"


class PublicResponseCreateThrottle(_PublicBaseThrottle):
    """POST create response — per IP, per survey (strict; ~20/min)."""

    scope = "public_response_create"
    rate = "20/min"
    key_kwarg = "survey_id"


class PublicResponseEditThrottle(_PublicBaseThrottle):
    """PATCH / finalize — per IP, per response (~120/min)."""

    scope = "public_response_edit"
    rate = "120/min"
    key_kwarg = "response_id"


class PublicDisplayThrottle(_PublicBaseThrottle):
    """POST display — per IP, per survey (~60/min)."""

    scope = "public_display"
    rate = "60/min"
    key_kwarg = "survey_id"
