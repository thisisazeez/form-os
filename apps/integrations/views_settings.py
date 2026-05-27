"""
Server-rendered (HTMX) Integrations hub — a Settings tab.

Owner/admin only (RBAC action ``manage_integration`` — apps.common.permissions.can()).

Page (TENANT schema, mounted by config/urls.py under /<org_slug>/):
  /<org_slug>/settings/integrations  GET — the hub (Slack + Webhooks card + deferred cards)
                                     POST — Slack connect / update / disconnect.

The hub is a REAL hub:
  - **Slack** — fully functional: paste an Incoming Webhook URL, pick events + survey(s),
    connect/update/disconnect. On a subscribed event the platform POSTs a Block-Kit message.
  - **Webhooks** — surfaced by LINK to the existing /settings/webhooks page (NOT duplicated).
  - **Deferred** integrations (Google Sheets, Airtable, Notion, Zapier/Make/n8n) — read-only
    descriptive cards with NO connect buttons (they need third-party OAuth/app credentials).

Slack integrations are Environment-scoped; the page operates on the caller's CURRENT workspace
production Environment (same resolution as the webhooks page).
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
from apps.surveys.models import Survey
from apps.webhooks.enums import WebhookEvent
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment

from . import services
from .catalog import DEFERRED_INTEGRATIONS
from .models import SlackIntegration


@login_required
@require_http_methods(["GET", "POST"])
def integrations_page(request):
    """``/<org_slug>/settings/integrations`` — the Integrations hub (owner/admin)."""
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    if not can(request.user, "manage_integration"):
        return render(request, "organizations/not_a_member.html", {"org": org}, status=403)

    environment = _target_environment(request)

    if request.method == "POST":
        return _handle_post(request, org, environment)

    slack_integrations = []
    surveys = []
    if environment is not None:
        slack_integrations = list(
            SlackIntegration.objects.filter(environment=environment).order_by("-created_at")
        )
        surveys = list(
            Survey.objects.filter(environment=environment)
            .order_by("name")
            .values("id", "name", "status")
        )

    return render(
        request,
        "settings/integrations.html",
        {
            "org": org,
            "section": "integrations",
            "environment": environment,
            "slack_integrations": slack_integrations,
            "surveys": surveys,
            "event_choices": WebhookEvent.choices,  # [(value, label), ...]
            "deferred_integrations": DEFERRED_INTEGRATIONS,
            "no_workspace": environment is None,
        },
    )


def _handle_post(request, org, environment):
    """Slack connect / update / disconnect dispatch (form ``op`` field). Post-redirect-get."""
    op = request.POST.get("op")
    redirect_url = f"/{org.slug}/settings/integrations"

    if not can(request.user, "manage_integration"):
        return redirect(redirect_url)

    if op == "slack_connect":
        if environment is None:
            return redirect(redirect_url)
        url = (request.POST.get("incoming_webhook_url") or "").strip()
        events = request.POST.getlist("events")
        survey_ids = request.POST.getlist("survey_ids")
        name = (request.POST.get("name") or "").strip()
        if url:
            integration = services.create_slack_integration(
                environment=environment,
                incoming_webhook_url=url,
                events=events,
                name=name,
                survey_ids=survey_ids,
                enabled=True,
                created_by_user_id=request.user.id,
            )
            audit.record_from_request(
                request,
                action=audit.ACTION_INTEGRATION_SLACK_CONNECTED,
                target_type="slack_integration",
                target_id=integration.id,
                metadata={"name": integration.name, "events": integration.events},
            )
        return redirect(redirect_url)

    if op == "slack_update":
        integration = _get_scoped_integration(request, environment)
        if integration is not None:
            services.update_slack_integration(
                integration=integration,
                incoming_webhook_url=(request.POST.get("incoming_webhook_url") or "").strip()
                or integration.incoming_webhook_url,
                events=request.POST.getlist("events"),
                survey_ids=request.POST.getlist("survey_ids"),
                name=request.POST.get("name") or "",
                enabled=request.POST.get("enabled") == "on",
            )
            audit.record_from_request(
                request,
                action=audit.ACTION_INTEGRATION_SLACK_UPDATED,
                target_type="slack_integration",
                target_id=integration.id,
                metadata={"name": integration.name, "events": integration.events},
            )
        return redirect(redirect_url)

    if op == "slack_disconnect":
        integration = _get_scoped_integration(request, environment)
        if integration is not None:
            integration_id = str(integration.id)
            integration_name = integration.name
            services.delete_slack_integration(integration=integration)
            audit.record_from_request(
                request,
                action=audit.ACTION_INTEGRATION_SLACK_DISCONNECTED,
                target_type="slack_integration",
                target_id=integration_id,
                metadata={"name": integration_name},
            )
        return redirect(redirect_url)

    return redirect(redirect_url)


# --- helpers ----------------------------------------------------------------------------- #
def _get_scoped_integration(request, environment) -> SlackIntegration | None:
    """Resolve the form's integration, scoped to the active environment (defense in depth)."""
    integration = SlackIntegration.objects.filter(
        pk=request.POST.get("integration_id")
    ).first()
    if integration is None:
        return None
    if environment is not None and integration.environment_id != environment.id:
        return None
    return integration


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
