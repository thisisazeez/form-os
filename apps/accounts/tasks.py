"""
Celery tasks for the accounts app — currently just transactional email delivery.

Defined with ``@shared_task`` so the project's Celery app (``config/celery.py``,
``autodiscover_tasks()``) registers them without importing the app object here. The task
is **eager-friendly**: with ``CELERY_TASK_ALWAYS_EAGER=True`` (dev + CI) ``.delay()`` runs
inline in-process, so callers (and tests asserting on ``mail.outbox``) get synchronous
behavior with no broker/worker. In prod the same ``.delay()`` enqueues to Redis.

We send pre-rendered content here; the message *content* is built by ``apps.accounts.emails``.
Keeping the task dumb (subject / plaintext / optional HTML / recipient) means it serializes
cleanly to JSON and needs no model/schema context to run on a worker.
"""
from __future__ import annotations

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives


@shared_task(ignore_result=True)
def send_mail_task(
    *, subject: str, message: str, recipient: str, html_message: str | None = None
) -> None:
    """Send one email (plaintext + optional HTML alternative) via the configured EMAIL_BACKEND.

    When Postmark is the transport, every message must declare a message stream — we attach
    the ``X-PM-Message-Stream`` header (configurable via ``POSTMARK_MESSAGE_STREAM``). The
    header is harmless on the console/locmem backends used in dev/CI.
    """
    headers: dict[str, str] = {}
    stream = getattr(settings, "POSTMARK_MESSAGE_STREAM", "")
    if stream:
        headers["X-PM-Message-Stream"] = stream

    email = EmailMultiAlternatives(
        subject=subject,
        body=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[recipient],
        headers=headers or None,
    )
    if html_message:
        email.attach_alternative(html_message, "text/html")
    email.send(fail_silently=False)
