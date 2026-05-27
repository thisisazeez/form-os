"""
Follow-up email delivery — the async Celery task that renders + sends one follow-up email.

``@shared_task`` (eager-friendly): with ``CELERY_TASK_ALWAYS_EAGER=True`` (dev + CI) ``.delay()``
runs inline so tests assert on ``django.core.mail.outbox`` (locmem) with no broker/worker; in
prod the same ``.delay()`` enqueues to Redis (config/celery.py autodiscovers this module).

REUSES THE EXISTING EMAIL INFRA: the actual transmit goes through the SAME eager-friendly
``apps.accounts.tasks.send_mail_task`` that verification/invitation emails use — which builds
an ``EmailMultiAlternatives`` (plaintext + HTML), attaches the Postmark message-stream header,
and sends via the configured ``EMAIL_BACKEND`` (console/dev, locmem/tests, Postmark/prod). We
do NOT re-implement transport; we render the body here (HTML) + a plaintext fallback and hand
it to that sender. The email SEND is the "mocked" part (per the product owner: "ignore emailing
check, codes can be read from DB") — but it travels the real pipeline, so locmem/console see it.

TENANT CONTEXT: like the webhook task, delivery runs OUTSIDE the request tenant schema (a
worker), so the dispatcher passes ``schema_name`` and we re-activate it to load + update the
FollowUpDelivery row. The outcome (SENT/FAILED) is recorded on that row; a failure NEVER
propagates (it is recorded, not raised — the finalize that triggered this is long done).
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone
from django_tenants.utils import schema_context

logger = logging.getLogger(__name__)


def _plaintext_from_html(html: str) -> str:
    """A minimal plaintext fallback for the HTML body (strip tags; collapse whitespace)."""
    import re

    text = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</\s*(p|div|li|h[1-6])\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@shared_task(ignore_result=True)
def deliver_follow_up(
    *,
    schema_name: str,
    delivery_id: str,
    follow_up: dict,
    answers: dict,
    active_lang: str,
    default_lang_code: str,
) -> None:
    """Render + send one follow-up email, recording SENT/FAILED on its FollowUpDelivery row.

    Re-activates ``schema_name`` (worker runs outside the request tenant). Renders the body
    here (the subject was rendered + stored at dispatch); sends via the shared
    ``send_mail_task`` (eager in dev/CI → ``mail.outbox``). Records the outcome; never raises.
    """
    from apps.accounts.tasks import send_mail_task

    from .enums import FollowUpDeliveryStatus
    from .models import FollowUpDelivery
    from .services import render_body, render_subject

    with schema_context(schema_name):
        delivery = FollowUpDelivery.objects.filter(pk=delivery_id).first()
        if delivery is None:
            return  # deleted between dispatch and delivery — nothing to do.

        delivery.attempts = (delivery.attempts or 0) + 1
        delivery.last_attempt_at = timezone.now()

        if not delivery.recipient_email:
            # Should not happen (dispatch records SKIPPED inline), but fail closed.
            delivery.status = FollowUpDeliveryStatus.SKIPPED
            delivery.save(update_fields=["attempts", "last_attempt_at", "status", "updated_at"])
            return

        action = follow_up.get("action") or {}
        try:
            subject = render_subject(
                action,
                answers=answers or {},
                active_lang=active_lang,
                default_lang_code=default_lang_code,
            ) or delivery.rendered_subject
            html_body = render_body(
                action,
                answers=answers or {},
                active_lang=active_lang,
                default_lang_code=default_lang_code,
            )
            plaintext = _plaintext_from_html(html_body) or subject

            # Reuse the existing email sender (eager in dev/CI → mail.outbox).
            send_mail_task(
                subject=subject,
                message=plaintext,
                recipient=delivery.recipient_email,
                html_message=html_body or None,
            )
        except Exception as exc:  # noqa: BLE001 — record + stop; never propagate.
            delivery.status = FollowUpDeliveryStatus.FAILED
            delivery.error = str(exc)[:1000]
            delivery.save(
                update_fields=["attempts", "last_attempt_at", "status", "error", "updated_at"]
            )
            logger.warning("follow-up delivery %s failed: %s", delivery_id, exc)
            return

        delivery.status = FollowUpDeliveryStatus.SENT
        delivery.rendered_subject = subject[:998]
        delivery.error = ""
        delivery.save(
            update_fields=[
                "attempts", "last_attempt_at", "status", "rendered_subject", "error", "updated_at",
            ]
        )
