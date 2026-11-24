"""Brand template tags — read the kernel directly from ``settings.BRAND``.

A tag (rather than a context processor) is deliberate: email templates are rendered with
``render_to_string()`` and have no request context, but still need the product name and
palette. Keeping access here means every render path — HTML, email, direct template tests —
resolves the same tokens from ``brand/tokens.json`` via ``config.settings.base.BRAND``.

Usage::

    {% load brand %}
    <title>{% block title %}Surveys — {% brand_name %}{% endblock %}</title>
    <a href="/" class="font-display text-lg font-bold">{% brand_wordmark %}</a>
    <script>tailwind.config = { theme: { extend: { colors: {
        brand: '{% brand_token "brand" %}',
        fontFamily: { display: [{% brand_font "display" %}] }
    } } } }</script>
"""
from __future__ import annotations

from typing import Any

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()


def _tokens() -> dict[str, Any]:
    return getattr(settings, "BRAND", {})


@register.simple_tag
def brand_name() -> str:
    """Product name, e.g. ``onForm``."""
    return str(_tokens().get("name", "onForm"))


@register.simple_tag
def brand_tagline() -> str:
    """Short marketing tagline."""
    return str(_tokens().get("tagline", ""))


@register.simple_tag
def brand_prefix() -> str:
    """First half of the wordmark, e.g. ``on`` (for inline-style contexts like email)."""
    return str(_tokens().get("wordmark", {}).get("prefix", "on"))


@register.simple_tag
def brand_accent() -> str:
    """Accented half of the wordmark, e.g. ``Form``."""
    return str(_tokens().get("wordmark", {}).get("accent", "Form"))


@register.simple_tag
def brand_token(token: str) -> str:
    """A palette value by key, e.g. ``{% brand_token "brand-hover" %}``."""
    return str(_tokens()["colors"][token])


@register.simple_tag
def brand_font(family: str) -> str:
    """A JS-ready font-family list, e.g. ``'Plus Jakarta Sans', 'sans-serif'``.

    Marked safe because the quotes must reach the inline <script> verbatim; values come
    from the repo-authored token file, not from user input.
    """
    fonts = _tokens()["fonts"][family]
    return mark_safe(", ".join(f"'{font}'" for font in fonts))  # noqa: S308


@register.inclusion_tag("brand/wordmark.html")
def brand_wordmark() -> dict[str, str]:
    """Render the two-part wordmark (accent span included)."""
    wordmark = _tokens().get("wordmark", {"prefix": "on", "accent": "Form"})
    return {"prefix": str(wordmark["prefix"]), "accent": str(wordmark["accent"])}
