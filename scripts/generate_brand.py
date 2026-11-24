#!/usr/bin/env python3
"""Generate the frontend brand constants from ``brand/tokens.json``.

``brand/tokens.json`` is the single authored source for the product name, wordmark,
palette, and typography. Django reads it directly in ``config.settings.base``; the
browser bundles get a generated TypeScript module so no runtime JSON fetch (or Vite
fs allow-listing outside ``frontend/``) is needed.

Usage:
    python3 scripts/generate_brand.py
    npm --prefix frontend run brand:generate
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS_FILE = ROOT / "brand" / "tokens.json"
OUTPUT_FILE = ROOT / "frontend" / "src" / "lib" / "brand.ts"


def ts(value: object) -> str:
    """JSON is valid TypeScript for these plain data values."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def main() -> None:
    tokens = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))

    body = (
        "// Generated from brand/tokens.json by scripts/generate_brand.py — do not edit.\n"
        "// Run `npm --prefix frontend run brand:generate` after changing the tokens.\n"
        "export const BRAND = "
        + ts(tokens)
        + " as const;\n\n"
        "export type BrandColor = keyof typeof BRAND.colors;\n"
    )
    OUTPUT_FILE.write_text(body, encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
