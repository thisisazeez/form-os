"""
Server-rendered (HTMX) API-key management — a Settings tab.

The browser counterpart of the (programmatic) API-key feature. Owner/admin only (RBAC action
``view_apikey`` / ``create_apikey`` / ``revoke_apikey`` — apps.common.permissions.can()).

Pages (TENANT schema, mounted by config/urls.py under /<org_slug>/):
  /<org_slug>/settings/api-keys                 GET   — list keys (+ POST create / revoke / delete).

Keys are Environment-scoped (models.md §2.15). The page operates on the caller's CURRENT
workspace (apps.common.active_workspace) and its production Environment — the natural target for
a programmatic key. The freshly-created RAW key is shown ONCE (carried in the session across the
post-redirect-get so a refresh doesn't re-leak it); only its hash is ever stored.
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
from .models import ApiKey

# Session key carrying the one-time raw secret across the post-create redirect.
_NEW_KEY_SESSION = "apikeys:new_raw_key"


@login_required
@require_http_methods(["GET", "POST"])
def api_keys_page(request):
    """``/<org_slug>/settings/api-keys`` — list + create + revoke/delete API keys (owner/admin)."""
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    # RBAC: owner/admin only. can() reads the routed org's Membership; plain members → 403 page.
    if not can(request.user, "view_apikey"):
        return render(request, "organizations/not_a_member.html", {"org": org}, status=403)

    environment = _target_environment(request)

    if request.method == "POST":
        return _handle_post(request, org, environment)

    keys = []
    if environment is not None:
        keys = list(
            ApiKey.objects.filter(environment=environment).order_by("-created_at")
        )

    # Pop the one-time raw key (shown once, immediately after creation).
    new_raw_key = request.session.pop(_NEW_KEY_SESSION, None)

    return render(
        request,
        "settings/api_keys.html",
        {
            "org": org,
            "section": "api-keys",
            "keys": keys,
            "environment": environment,
            "new_raw_key": new_raw_key,
            "no_workspace": environment is None,
        },
    )


def _handle_post(request, org, environment):
    """Create / revoke / delete dispatch (form ``op`` field). Redirects (post-redirect-get)."""
    op = request.POST.get("op")
    redirect_url = f"/{org.slug}/settings/api-keys"

    if op == "create":
        if not can(request.user, "create_apikey") or environment is None:
            return redirect(redirect_url)
        label = (request.POST.get("label") or "").strip() or "API key"
        api_key, raw_key = services.create_api_key(
            environment=environment,
            label=label,
            created_by_user_id=request.user.id,
        )
        request.session[_NEW_KEY_SESSION] = raw_key
        audit.record_from_request(
            request,
            action=audit.ACTION_APIKEY_CREATED,
            target_type="apikey",
            target_id=api_key.id,
            metadata={"label": api_key.label},
        )
        return redirect(redirect_url)

    if op in ("revoke", "delete"):
        if not can(request.user, "revoke_apikey"):
            return redirect(redirect_url)
        key_id = request.POST.get("key_id")
        api_key = ApiKey.objects.filter(pk=key_id).first()
        if api_key is not None and (environment is None or api_key.environment_id == environment.id):
            if op == "revoke":
                services.revoke_api_key(api_key=api_key)
            else:
                api_key.delete()
            audit.record_from_request(
                request,
                action=audit.ACTION_APIKEY_REVOKED,
                target_type="apikey",
                target_id=key_id,
                metadata={"op": op, "label": api_key.label},
            )
        return redirect(redirect_url)

    return redirect(redirect_url)


# --- helpers ----------------------------------------------------------------------------- #
def _target_environment(request) -> Environment | None:
    """The Environment a key targets: the current workspace's production env (else its first)."""
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
