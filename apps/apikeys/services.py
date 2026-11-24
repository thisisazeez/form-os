"""
API-key service layer (TENANT schema) — secret generation, hashing, create + revoke.

The single home for ApiKey business logic. Views (HTMX management UI) and tests call these;
no surface mints or hashes keys on its own (so the "store only a hash" invariant — REVIEW.md
§3.6, models.md §2.15 — lives in exactly one place).

KEY SHAPE (non-secret prefix + secret body):
    osk_<16 hex>...<rest>          e.g.  osk_a1b2c3d4e5f6a7b8c9d0...
  - ``prefix``      : the first 12 chars (``osk_`` + 8 hex) — stored plaintext, shown in lists
                      so an admin can recognize a key without ever seeing the secret again.
  - ``hashed_key``  : SHA-256 hex of the FULL raw key — the only secret material persisted.

The raw key is returned ONCE from ``create_api_key`` (the view shows it a single time); it is
never stored and cannot be recovered. Authentication (apps.apikeys.authentication) hashes the
presented header value the same way and looks up ``hashed_key``.
"""
from __future__ import annotations

import hashlib
import secrets

from django.utils import timezone

from .models import ApiKey

__all__ = [
    "PREFIX",
    "hash_key",
    "generate_raw_key",
    "create_api_key",
    "revoke_api_key",
]

# Public, non-secret brand prefix on every key ("onform key").
PREFIX = "osk_"
# Length (chars) of the stored, non-secret prefix kept for list display.
_PREFIX_DISPLAY_LEN = 12
# Bytes of entropy in the secret body (32 bytes → 64 hex chars; well above brute-force range).
_SECRET_BYTES = 32


def hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of a raw key — the only key material we persist or compare.

    Deterministic (no per-key salt) ON PURPOSE: authentication must look a key up by its hash
    in one indexed query. The raw key carries 256 bits of entropy, so an unsalted fast hash is
    not the weakness it would be for a low-entropy password (REVIEW.md §3.6).
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_raw_key() -> str:
    """Mint a fresh random raw key string (``osk_`` + 64 hex chars). Cryptographically random."""
    return f"{PREFIX}{secrets.token_hex(_SECRET_BYTES)}"


def create_api_key(*, environment, label: str, created_by_user_id=None, scopes=None):
    """Create an ApiKey for ``environment`` and return ``(api_key, raw_key)``.

    The RAW key is returned ONLY here (shown once by the caller) — only its hash is stored.
    Must be called with the tenant schema ACTIVE (ApiKey is a tenant-schema model).
    """
    raw_key = generate_raw_key()
    api_key = ApiKey.objects.create(
        environment=environment,
        label=label,
        hashed_key=hash_key(raw_key),
        prefix=raw_key[:_PREFIX_DISPLAY_LEN],
        scopes=scopes or [],
        created_by_user_id=created_by_user_id,
    )
    return api_key, raw_key


def revoke_api_key(*, api_key: ApiKey) -> ApiKey:
    """Soft-revoke ``api_key`` (set ``revoked_at``). Idempotent — re-revoking is a no-op.

    Revoked keys are rejected by the authenticator (it checks ``revoked_at``). We keep the row
    (not a hard delete) so the audit log + management UI retain its history.
    """
    if api_key.revoked_at is None:
        api_key.revoked_at = timezone.now()
        api_key.save(update_fields=["revoked_at", "updated_at"])
    return api_key
