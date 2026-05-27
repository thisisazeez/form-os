"""
Webhook delivery — the async Celery task that POSTs a signed payload to a subscriber endpoint.

``@shared_task`` (eager-friendly): with ``CELERY_TASK_ALWAYS_EAGER=True`` (dev + CI) ``.delay()``
runs inline so tests can assert on a mocked HTTP call with no broker/worker; in prod the same
``.delay()`` enqueues to Redis (config/celery.py autodiscovers this module).

TENANT CONTEXT: delivery runs OUTSIDE the request's tenant schema (a worker process), so the
dispatcher (apps.webhooks.services.dispatch_event) passes ``schema_name`` and we re-activate it
before loading the Webhook + recording the result (cross-schema worker pattern).

SIGNING: the body is the canonical JSON of an envelope ``{event, data, webhook_id, delivered_at}``;
we send an ``X-OpenSurvey-Signature: sha256=<hmac-hex>`` header (HMAC-SHA256 of the raw body keyed
by the webhook's ``secret``) so the receiver can verify authenticity.

RETRY + FAILURE RECORDING: transient failures (network / 5xx) retry with exponential backoff
(bounded ``max_retries``). Every terminal outcome updates the Webhook's delivery-health fields
(``last_delivery_at`` / ``last_status_code`` / ``last_error`` / ``consecutive_failures``); a key
crossing the failure ceiling is auto-disabled so a dead endpoint stops generating churn.

HTTP CLIENT: Python's stdlib ``urllib.request`` — the project ships no third-party HTTP client
and the tests mock at this boundary (``apps.webhooks.tasks._http_post``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime

from celery import shared_task
from django.utils import timezone
from django_tenants.utils import schema_context

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-OpenSurvey-Signature"
EVENT_HEADER = "X-OpenSurvey-Event"
_REQUEST_TIMEOUT_SECONDS = 10
# Auto-disable an endpoint after this many consecutive failures (stop churning on dead URLs).
_FAILURE_CEILING = 10


def sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 of ``body`` keyed by ``secret`` → ``sha256=<hex>`` (the signature header)."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _http_post(url: str, body: bytes, headers: dict[str, str]) -> int:
    """POST ``body`` to ``url`` with ``headers``; return the HTTP status code.

    Isolated so tests mock exactly this (no real network). Raises on transport errors and on
    non-2xx HTTP (urllib raises HTTPError for >=400), which the task maps to retry/failure.
    """
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
        return resp.status


@shared_task(
    bind=True,
    ignore_result=True,
    max_retries=4,
    default_retry_delay=10,
    retry_backoff=True,
)
def deliver_webhook(self, *, schema_name: str, webhook_id: str, event: str, payload: dict) -> None:
    """Deliver one webhook event to one endpoint, recording the outcome on the Webhook row.

    Re-activates ``schema_name`` (worker runs outside the request tenant), builds + signs the
    envelope, POSTs it, then records success/failure. Transient failures retry; a permanent
    failure (or exhausted retries) is recorded and may auto-disable the endpoint.
    """
    from .models import Webhook

    with schema_context(schema_name):
        webhook = Webhook.objects.filter(pk=webhook_id).first()
        if webhook is None or not webhook.enabled:
            return  # deleted/disabled between dispatch and delivery — nothing to do.

        envelope = {
            "event": event,
            "data": payload,
            "webhook_id": str(webhook.id),
            "delivered_at": datetime.now(UTC).isoformat(),
        }
        body = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            EVENT_HEADER: event,
            SIGNATURE_HEADER: sign_payload(webhook.secret, body),
        }

        try:
            status_code = _http_post(webhook.url, body, headers)
        except urllib.error.HTTPError as exc:
            # Server returned an HTTP error status. 5xx → transient (retry); 4xx → permanent.
            status_code = exc.code
            _record_failure(webhook, status_code, f"HTTP {status_code}")
            if status_code >= 500:
                raise self.retry(exc=exc) from exc
            return
        except Exception as exc:  # noqa: BLE001 — network/transport error → transient, retry.
            _record_failure(webhook, None, str(exc))
            raise self.retry(exc=exc) from exc

        _record_success(webhook, status_code)


# --- outcome recording -------------------------------------------------------------------- #
def _record_success(webhook, status_code: int) -> None:
    webhook.last_delivery_at = timezone.now()
    webhook.last_status_code = status_code
    webhook.last_error = ""
    webhook.consecutive_failures = 0
    webhook.save(
        update_fields=[
            "last_delivery_at", "last_status_code", "last_error",
            "consecutive_failures", "updated_at",
        ]
    )


def _record_failure(webhook, status_code: int | None, error: str) -> None:
    webhook.last_delivery_at = timezone.now()
    webhook.last_status_code = status_code
    webhook.last_error = (error or "")[:1000]
    webhook.consecutive_failures = (webhook.consecutive_failures or 0) + 1
    update_fields = [
        "last_delivery_at", "last_status_code", "last_error",
        "consecutive_failures", "updated_at",
    ]
    # Auto-disable a chronically failing endpoint so it stops generating delivery churn.
    if webhook.consecutive_failures >= _FAILURE_CEILING and webhook.enabled:
        webhook.enabled = False
        update_fields.append("enabled")
    webhook.save(update_fields=update_fields)
