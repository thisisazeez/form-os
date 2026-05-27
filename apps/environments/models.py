"""
environments — TENANT schema placeholder package.

# Wave 2: NO models here. The `Environment` model lives in apps/workspaces/models.py
# per contracts/models.md PART B (`workspaces` app owns Workspace + Environment, §2.1–§2.2).

This package exists only because the Phase-0 scaffold brief listed `environments` among the
tenant apps. It is an intentional empty placeholder; do not add an Environment model here —
that would split a model away from its frozen app placement and break migration ownership.
If a future re-broadcast moves Environment into its own app, that is a coordinated
contract change (PLAN §15), not a scaffold decision.
"""
