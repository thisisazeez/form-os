"""
Slack delivery — the async Celery task that POSTs a Block-Kit message to a Slack Incoming Webhook.

``@shared_task`` (eager-friendly): with ``CELERY_TASK_ALWAYS_EAGER=True`` (dev + CI) ``.delay()``
runs inline so tests assert on a mocked HTTP call with no broker/worker; in prod the same
``.delay()`` enqueues to Redis (config/celery.py autodiscovers this module).

TENANT CONTEXT: delivery runs OUTSIDE the request's tenant schema (a worker process), so the
dispatcher (apps.integrations.services.dispatch_event) passes ``schema_name`` and we re-activate
it before loading the SlackIntegration + recording the result (cross-schema worker pattern —
identical to apps.webhooks.tasks).

NO SIGNING: Slack Incoming Webhooks do not verify an HMAC (the secret is the unguessable URL
itself), so unlike webhooks there is no signature header.

RETRY + FAILURE RECORDING: transient failures (network / 5xx) retry with bounded backoff; every
terminal outcome updates the row's delivery-health fields; an endpoint crossing the failure
ceiling is auto-disabled so a dead URL stops generating churn. Mirrors apps.webhooks.tasks.

HTTP CLIENT: stdlib ``urllib.request`` (the project ships no third-party HTTP client); tests mock
exactly ``apps.integrations.tasks._http_post``.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from celery import shared_task
from django.utils import timezone
from django_tenants.utils import schema_context

from .slack_message import build_slack_message

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10
# Auto-disable a destination after this many consecutive failures (stop churning on dead URLs).
_FAILURE_CEILING = 10


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
def deliver_slack(self, *, schema_name: str, integration_id: str, event: str, payload: dict) -> None:
    """Deliver one event to one Slack Incoming Webhook, recording the outcome on the row.

    Re-activates ``schema_name`` (worker runs outside the request tenant), builds the Slack
    Block-Kit body, POSTs it, then records success/failure. Transient failures retry; a
    permanent failure (or exhausted retries) is recorded and may auto-disable the destination.
    """
    from .models import SlackIntegration

    with schema_context(schema_name):
        integration = SlackIntegration.objects.filter(pk=integration_id).first()
        if integration is None or not integration.enabled:
            return  # deleted/disabled between dispatch and delivery — nothing to do.

        message = build_slack_message(event, payload)
        body = json.dumps(message, separators=(",", ":"), default=str).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        try:
            status_code = _http_post(integration.incoming_webhook_url, body, headers)
        except urllib.error.HTTPError as exc:
            # Server returned an HTTP error status. 5xx → transient (retry); 4xx → permanent.
            status_code = exc.code
            _record_failure(integration, status_code, f"HTTP {status_code}")
            if status_code >= 500:
                raise self.retry(exc=exc) from exc
            return
        except Exception as exc:  # noqa: BLE001 — network/transport error → transient, retry.
            _record_failure(integration, None, str(exc))
            raise self.retry(exc=exc) from exc

        _record_success(integration, status_code)


# --- outcome recording -------------------------------------------------------------------- #
def _record_success(integration, status_code: int) -> None:
    integration.last_delivery_at = timezone.now()
    integration.last_status_code = status_code
    integration.last_error = ""
    integration.consecutive_failures = 0
    integration.save(
        update_fields=[
            "last_delivery_at", "last_status_code", "last_error",
            "consecutive_failures", "updated_at",
        ]
    )


def _record_failure(integration, status_code: int | None, error: str) -> None:
    integration.last_delivery_at = timezone.now()
    integration.last_status_code = status_code
    integration.last_error = (error or "")[:1000]
    integration.consecutive_failures = (integration.consecutive_failures or 0) + 1
    update_fields = [
        "last_delivery_at", "last_status_code", "last_error",
        "consecutive_failures", "updated_at",
    ]
    # Auto-disable a chronically failing destination so it stops generating delivery churn.
    if integration.consecutive_failures >= _FAILURE_CEILING and integration.enabled:
        integration.enabled = False
        update_fields.append("enabled")
    integration.save(update_fields=update_fields)
