#!/usr/bin/env python3
"""Fail on stale OpenSurvey branding outside the explicit compatibility allowlist.

Phase 2 renames display strings and identifiers; Phase 4 will retire the allowlisted
public contracts (widget global, mount IDs, webhook headers, API key prefix, edit-token
salt, healthz service, Docker volume). Until then those literals are intentionally kept.

Usage:
    python3 scripts/brand_lint.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN = ("OpenSurvey", "opensurvey", "openSurvey")

# Public/migration-sensitive strings deliberately deferred to Phase 4, plus the one
# historical attribution reference in README/LICENSE.
ALLOWED_LITERALS = (
    "opensurvey-widget.js",
    "opensurvey-widget",
    "opensurvey-builder-config",
    "opensurvey-runtime-config",
    "opensurvey-builder",
    "opensurvey-runtime",
    "opensurvey_pgdata",
    "opensurvey.responses.edit_token",
    "OpenSurveyWidget",
    "OpenSurveyPublicApi",
    "OpenSurveyDeps",
    "openSurvey",
    "X-OpenSurvey-Signature",
    "X-OpenSurvey-Event",
    '"service": "opensurvey"',
    "https://github.com/mohitbadwal/opensurvey",
    "[OpenSurvey](",
    "Based on OpenSurvey (MIT)",
)

ALLOWED_PATHS = {
    "docs/onform-rebrand-plan.md",
    "scripts/brand_lint.py",
}

EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".html", ".json", ".md", ".yml", ".yaml", ".sh", ".txt",
    ".toml", ".css", ".cfg", ".ini", ".example",
}
EXCLUDED_DIR_PARTS = {
    ".git", "node_modules", "dist", "staticfiles", ".venv",
    "__pycache__", "screenshots", ".pytest_cache",
}


def main() -> int:
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT).as_posix()
        if (
            not path.is_file()
            or path.suffix not in EXTENSIONS
            or rel in ALLOWED_PATHS
            or any(part in EXCLUDED_DIR_PARTS for part in path.relative_to(ROOT).parts)
        ):
            continue

        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            cleaned = line
            for allowed in ALLOWED_LITERALS:
                cleaned = cleaned.replace(allowed, "")
            if any(word in cleaned for word in FORBIDDEN):
                problems.append(f"{rel}:{lineno}: {line.strip()}")

    if problems:
        print(f"brand lint: {len(problems)} stale reference(s)")
        for problem in problems[:60]:
            print(" ", problem)
        return 1
    print("brand lint: clean (compatibility allowlist only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
