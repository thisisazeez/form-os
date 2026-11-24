"""Contract tests for the onForm brand kernel.

The kernel has one authored source (``brand/tokens.json``) and two generated consumers
(Django settings + ``frontend/src/lib/brand.ts``). These tests keep them honest so a
future palette edit cannot silently leave the browser bundle on the old colors.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.template import Context, Template

ROOT = Path(__file__).resolve().parent.parent


def test_settings_expose_the_token_file_verbatim():
    tokens = json.loads((ROOT / "brand" / "tokens.json").read_text(encoding="utf-8"))
    assert settings.BRAND == tokens
    assert settings.BRAND["name"] == "onForm"
    assert settings.BRAND["colors"]["brand"] == "#5B4BE1"
    assert settings.BRAND["colors"]["brand-hover"] == "#4838C4"


def test_generated_frontend_constants_are_in_sync():
    generated = (ROOT / "frontend" / "src" / "lib" / "brand.ts").read_text(encoding="utf-8")
    for key, value in settings.BRAND["colors"].items():
        assert value in generated, f"missing color token {key}={value} in brand.ts"
    assert settings.BRAND["tagline"] in generated
    assert settings.BRAND["wordmark"]["prefix"] in generated


def test_wordmark_tag_renders_from_tokens():
    html = Template("{% load brand %}{% brand_wordmark %}").render(Context())
    prefix = settings.BRAND["wordmark"]["prefix"]
    accent = settings.BRAND["wordmark"]["accent"]
    assert html.strip() == f'{prefix}<span class="text-brand">{accent}</span>'


def test_email_subjects_use_the_brand_name():
    from apps.accounts import emails

    assert emails._brand_name() == settings.BRAND["name"]
