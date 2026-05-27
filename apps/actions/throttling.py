"""
Rate limiting for the PUBLIC widget APIs (FEATURE_PARITY WS-11) — mirrors the response-runtime
throttles (apps.responses.throttling): subclass DRF's ``SimpleRateThrottle`` with a HARDCODED
rate and a per-IP + per-environment cache key (read from the resolved view kwarg
``environment_id``). Hardcoding keeps this self-contained (no settings change) and makes the
scoping explicit + reviewable in one place.

The widget calls these on every tracked action / page load, so the buckets are generous but
bounded (a misbehaving page can't hammer the env). locmem cache in dev/CI is fine (short
sliding window); tests clear the cache between cases.
"""
from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class _WidgetBaseThrottle(SimpleRateThrottle):
    """Base for the widget throttles. Subclasses set ``scope`` + ``rate`` (hardcoded)."""

    rate: str = ""
    key_kwarg = "environment_id"

    def get_rate(self) -> str:
        return self.rate

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        scope_value = view.kwargs.get(self.key_kwarg, "") if hasattr(view, "kwargs") else ""
        return f"throttle_widget_{self.scope}_{scope_value}_{ident}"


class WidgetTrackThrottle(_WidgetBaseThrottle):
    """POST actions/track — per IP, per environment (~120/min: a page may track several
    actions on load + interaction)."""

    scope = "widget_track"
    rate = "120/min"


class WidgetAppSurveysThrottle(_WidgetBaseThrottle):
    """GET app-surveys — per IP, per environment (~120/min: widget polls on navigation)."""

    scope = "widget_app_surveys"
    rate = "120/min"


class WidgetDisplayThrottle(_WidgetBaseThrottle):
    """POST app-survey display — per IP, per environment (~120/min: one impression per shown
    survey, several per page interaction)."""

    scope = "widget_display"
    rate = "120/min"
