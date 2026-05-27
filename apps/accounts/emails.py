"""
Transactional email senders for the auth + invitation flows (PLAN §9).

Email delivery goes through Django's mail framework, so the active ``EMAIL_BACKEND``
decides the transport: console in dev, ``locmem`` in CI (so tests assert on
``mail.outbox``), a real provider in prod. We keep the *content* here and the *transport*
in settings — these functions never care which backend is configured.

Sending is wrapped in a thin Celery task (:func:`send_mail_task`) that is **eager-friendly**:
with ``CELERY_TASK_ALWAYS_EAGER=True`` (dev + CI) the task runs inline in-process, so tests
see ``mail.outbox`` synchronously without a broker/worker. In prod the same call enqueues.

Links are **path-based** per ADR-001 and nested under the whitelisted ``/auth/`` prefix
(see the middleware deviation note in config/urls_public.py — first path segments other than
``auth`` are taken as org slugs by TenantSlugMiddleware):
  - verification / password-reset are public-schema flows → ``/auth/<page>?token=...``.
  - an invitation belongs to an org, but acceptance is a public flow; the link lands on the
    public ``/auth/invitations/accept`` page carrying the token (the page logs the invitee in
    or routes them to signup, then accepts).
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.template.loader import render_to_string

from .tasks import send_mail_task


def _name_suffix(name: str | None) -> str:
    """' Ada' for a personalised greeting, or '' — pre-spaced so 'Hi{{suffix}}' reads right."""
    return f" {name}" if name else ""


def _base_url() -> str:
    """Best-effort public base URL for links in emails.

    Configurable via ``PUBLIC_BASE_URL``; falls back to ``http://<BASE_DOMAIN>:8000`` which
    matches the local dev convention (see ``create_demo_tenant`` output). Trailing slash
    stripped so we can append paths cleanly.
    """
    explicit = getattr(settings, "PUBLIC_BASE_URL", None)
    if explicit:
        return explicit.rstrip("/")
    domain = getattr(settings, "BASE_DOMAIN", "localhost")
    return f"http://{domain}:8000"


def _send(subject: str, body: str, to_email: str, html: str | None = None) -> None:
    """Dispatch one email (plaintext + optional HTML) via the eager-friendly Celery task."""
    send_mail_task.delay(
        subject=subject,
        message=body,
        recipient=to_email,
        html_message=html,
    )


# --- concrete senders -------------------------------------------------------

def send_verification_email(user, token: str) -> None:
    link = f"{_base_url()}/auth/verify-email?{urlencode({'token': token})}"
    subject = "Verify your OpenSurvey email"
    body = (
        f"Hi{_name_suffix(user.name)},\n\n"
        "Welcome to OpenSurvey. Confirm your email address to activate your account:\n\n"
        f"  {link}\n\n"
        "If you didn't create this account, you can ignore this message.\n"
    )
    html = render_to_string(
        "email/verify.html", {"name_suffix": _name_suffix(user.name), "link": link}
    )
    _send(subject, body, user.email, html)


def send_password_reset_email(user, token: str) -> None:
    link = f"{_base_url()}/auth/reset-password?{urlencode({'token': token})}"
    subject = "Reset your OpenSurvey password"
    body = (
        f"Hi{_name_suffix(user.name)},\n\n"
        "We received a request to reset your OpenSurvey password. Use the link below "
        "to choose a new one:\n\n"
        f"  {link}\n\n"
        "If you didn't request this, no action is needed — your password is unchanged.\n"
    )
    html = render_to_string(
        "email/reset.html", {"name_suffix": _name_suffix(user.name), "link": link}
    )
    _send(subject, body, user.email, html)


def send_invitation_email(invitation) -> None:
    """Email an org invitation. ``invitation.token`` is the opaque DB token."""
    org = invitation.organization
    link = f"{_base_url()}/auth/invitations/accept?{urlencode({'token': invitation.token})}"
    subject = f"You're invited to {org.name} on OpenSurvey"
    expires = f"{invitation.expires_at:%Y-%m-%d %H:%M UTC}"
    body = (
        f"You've been invited to join '{org.name}' as a {invitation.role}.\n\n"
        "Accept the invitation here:\n\n"
        f"  {link}\n\n"
        "If you don't have a OpenSurvey account yet, you'll be able to create one first. "
        f"This invitation expires on {expires}.\n"
    )
    html = render_to_string(
        "email/invitation.html",
        {"org_name": org.name, "role": invitation.role, "link": link, "expires": expires},
    )
    _send(subject, body, invitation.email, html)


# Re-export so callers can `from .emails import send_mail` if they truly need the raw helper
# (none currently do; kept off the public surface intentionally).
__all__ = [
    "send_verification_email",
    "send_password_reset_email",
    "send_invitation_email",
]
