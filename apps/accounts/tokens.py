"""
Stateless, signed tokens for email verification and password reset (PLAN §9).

Both flows email a one-time link carrying a token. We use ``django.core.signing``
(``TimestampSigner`` via ``dumps``/``loads``) rather than a DB token model:

  - Stateless: no extra table; the token carries the user id + an expiry (``max_age``).
  - Tamper-evident: signed with ``SECRET_KEY`` + a per-purpose ``salt`` so a verify token
    can never be replayed as a reset token (different salts ⇒ ``BadSignature``).
  - Self-expiring: ``loads(..., max_age=...)`` raises ``SignatureExpired`` past the TTL.

Invitations use a different mechanism — the opaque ``Invitation.token`` column already
on the model (``secrets.token_urlsafe`` + DB ``expires_at``) — because an invitation is a
first-class, revocable record. See ``apps.organizations.services``.

TTLs are configurable via settings (``EMAIL_VERIFY_TTL_SECONDS`` /
``PASSWORD_RESET_TTL_SECONDS``); defaults: verify 7 days, reset 2 hours.
"""
from __future__ import annotations

from django.conf import settings
from django.core import signing

# Per-purpose salts keep the two token namespaces disjoint (a reset token can't verify an
# email and vice-versa). Changing a salt invalidates all outstanding tokens of that kind.
VERIFY_EMAIL_SALT = "accounts.verify-email"
PASSWORD_RESET_SALT = "accounts.password-reset"

_DEFAULT_VERIFY_TTL = 7 * 24 * 3600  # 7 days
_DEFAULT_RESET_TTL = 2 * 3600  # 2 hours


def _verify_ttl() -> int:
    return int(getattr(settings, "EMAIL_VERIFY_TTL_SECONDS", _DEFAULT_VERIFY_TTL))


def _reset_ttl() -> int:
    return int(getattr(settings, "PASSWORD_RESET_TTL_SECONDS", _DEFAULT_RESET_TTL))


def make_email_verification_token(user) -> str:
    """Signed token proving "verify this user's email". Carries the user id."""
    return signing.dumps({"uid": str(user.pk)}, salt=VERIFY_EMAIL_SALT)


def read_email_verification_token(token: str) -> str | None:
    """Return the user id encoded in a valid verification token, else ``None``.

    ``None`` on any tampering, wrong salt, or expiry — callers surface a generic 400 so
    the failure mode never leaks which of the three it was.
    """
    try:
        data = signing.loads(token, salt=VERIFY_EMAIL_SALT, max_age=_verify_ttl())
    except (signing.BadSignature, signing.SignatureExpired):
        return None
    return data.get("uid")


def make_password_reset_token(user) -> str:
    """Signed token authorizing a password reset for this user.

    Includes a slice of the current password hash so the token is single-use in practice:
    once the password changes, the embedded hash no longer matches and a replayed token is
    rejected by :func:`read_password_reset_token`.
    """
    payload = {"uid": str(user.pk), "h": _password_fingerprint(user)}
    return signing.dumps(payload, salt=PASSWORD_RESET_SALT)


def read_password_reset_token(token: str, *, get_user) -> object | None:
    """Resolve a reset token to its ``User``, or ``None`` if invalid/expired/already-used.

    ``get_user`` is a callable ``uid -> User | None`` (injected so this module stays free
    of model imports / schema concerns). The embedded password fingerprint must still match
    the user's current password — this makes the token one-shot across a password change.
    """
    try:
        data = signing.loads(token, salt=PASSWORD_RESET_SALT, max_age=_reset_ttl())
    except (signing.BadSignature, signing.SignatureExpired):
        return None

    uid = data.get("uid")
    if not uid:
        return None
    user = get_user(uid)
    if user is None:
        return None
    if data.get("h") != _password_fingerprint(user):
        # Password already changed since the token was issued → consume-once semantics.
        return None
    return user


def _password_fingerprint(user) -> str:
    """A short, non-reversible slice of the password hash for single-use binding.

    The hash itself is already a one-way derivation; we expose only a prefix so the token
    never carries enough to attack the hash, while still changing whenever the password does.
    """
    pwd = user.password or ""
    return pwd[-16:]
