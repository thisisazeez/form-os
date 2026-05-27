"""
Server-rendered (HTMX) webhook management — a Settings tab.

Owner/admin only (RBAC action ``manage_webhook`` — apps.common.permissions.can()).

Pages (TENANT schema, mounted by config/urls.py under /<org_slug>/):
  /<org_slug>/settings/webhooks  GET — list endpoints (+ POST create / update / delete).

Webhooks are Environment-scoped (models.md §2.14); the page operates on the caller's CURRENT
workspace production Environment. The signing secret is shown ONCE on creation (carried across
the post-redirect-get in the session). Subscribed events are the frozen WebhookEvent set.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name

from apps.audit import services as audit
from apps.common.active_workspace import current_workspace
from apps.common.permissions import can
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment

from . import services
from .enums import WebhookEvent
from .models import Webhook

# Session key carrying the one-time signing secret across the post-create redirect.
_NEW_SECRET_SESSION = "webhooks:new_secret"


@login_required
@require_http_methods(["GET", "POST"])
def webhooks_page(request):
    """``/<org_slug>/settings/webhooks`` — list + create + update + delete webhooks (owner/admin)."""
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    if not can(request.user, "manage_webhook"):
        return render(request, "organizations/not_a_member.html", {"org": org}, status=403)

    environment = _target_environment(request)

    if request.method == "POST":
        return _handle_post(request, org, environment)

    webhooks = []
    if environment is not None:
        webhooks = list(
            Webhook.objects.filter(environment=environment).order_by("-created_at")
        )

    new_secret = request.session.pop(_NEW_SECRET_SESSION, None)

    return render(
        request,
        "settings/webhooks.html",
        {
            "org": org,
            "section": "webhooks",
            "webhooks": webhooks,
            "environment": environment,
            "event_choices": WebhookEvent.choices,  # [(value, label), ...]
            "new_secret": new_secret,
            "no_workspace": environment is None,
        },
    )


def _handle_post(request, org, environment):
    """Create / update / delete dispatch (form ``op`` field). Redirects (post-redirect-get)."""
    op = request.POST.get("op")
    redirect_url = f"/{org.slug}/settings/webhooks"

    if not can(request.user, "manage_webhook"):
        return redirect(redirect_url)

    if op == "create":
        if environment is None:
            return redirect(redirect_url)
        url = (request.POST.get("url") or "").strip()
        events = request.POST.getlist("events")
        if url:
            webhook = services.create_webhook(
                environment=environment,
                url=url,
                events=events,
                enabled=True,
                created_by_user_id=request.user.id,
            )
            request.session[_NEW_SECRET_SESSION] = webhook.secret
            audit.record_from_request(
                request,
                action=audit.ACTION_WEBHOOK_CREATED,
                target_type="webhook",
                target_id=webhook.id,
                metadata={"url": webhook.url, "events": webhook.events},
            )
        return redirect(redirect_url)

    if op == "update":
        webhook = _get_scoped_webhook(request, environment)
        if webhook is not None:
            services.update_webhook(
                webhook=webhook,
                url=(request.POST.get("url") or "").strip() or webhook.url,
                events=request.POST.getlist("events"),
                enabled=request.POST.get("enabled") == "on",
            )
        return redirect(redirect_url)

    if op == "delete":
        webhook = _get_scoped_webhook(request, environment)
        if webhook is not None:
            webhook_url = webhook.url
            webhook_id = str(webhook.id)
            services.delete_webhook(webhook=webhook)
            audit.record_from_request(
                request,
                action=audit.ACTION_WEBHOOK_DELETED,
                target_type="webhook",
                target_id=webhook_id,
                metadata={"url": webhook_url},
            )
        return redirect(redirect_url)

    return redirect(redirect_url)


# --- helpers ----------------------------------------------------------------------------- #
def _get_scoped_webhook(request, environment) -> Webhook | None:
    """Resolve the form's webhook, scoped to the active environment (defense in depth)."""
    webhook = Webhook.objects.filter(pk=request.POST.get("webhook_id")).first()
    if webhook is None:
        return None
    if environment is not None and webhook.environment_id != environment.id:
        return None
    return webhook


def _target_environment(request) -> Environment | None:
    ws = current_workspace(request)
    if ws is None:
        return None
    return (
        Environment.objects.filter(workspace=ws, type=EnvType.PRODUCTION).first()
        or Environment.objects.filter(workspace=ws).order_by("type").first()
    )


def _routed_org(request):
    org = getattr(request, "tenant", None)
    if org is None or getattr(org, "schema_name", None) == get_public_schema_name():
        return None
    return org
