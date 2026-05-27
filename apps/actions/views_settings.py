"""
Server-rendered (form-POST) Actions management — a Settings tab (FEATURE_PARITY WS-11).

Lists / creates / edits / deletes the current workspace environment's ActionClasses. These are
the user-action definitions that TRIGGER website/app surveys (apps.actions.models.ActionClass);
the survey builder's Settings tab references them by id in ``Survey.triggers``.

RBAC is WORKSPACE-scoped (NOT org owner/admin, unlike webhooks): viewing needs workspace ``read``
(``view_action_class``), mutating needs workspace ``write`` (``create/edit/delete_action_class``).
We resolve the caller's CURRENT workspace production Environment (apps.common.active_workspace) and
pass it to ``can()`` so the action's owning workspace is the authorization target (rbac.md §7).

Pages (TENANT schema, mounted by config/urls.py under /<org_slug>/):
  /<org_slug>/settings/actions     GET — list (+ POST create / update / delete).
  /<org_slug>/settings/connection  GET — env id + the embed snippet (read-only; see views_settings
                                          ``connection_page`` — separate, no mutation).

Config shape per type (validated app-side in apps.actions.services.validate_action_config):
  code     → {key?}          (the SDK ``track(key)`` key; defaults to ``name``)
  pageview → {urlMatch?}     (widget-side URL match)
  click    → {cssSelector?}  (widget-side click match)
The form posts a single ``config_value`` field; we map it to the right config key for the type.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name

from apps.common.active_workspace import current_workspace
from apps.common.permissions import can
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment

from . import services
from .enums import ActionType
from .models import ActionClass

# The single form field name carrying the type-specific config value (key / urlMatch / cssSelector).
_CONFIG_FORM_FIELD = "config_value"

# The config KEY each ActionType stores its single form value under.
_CONFIG_KEY_BY_TYPE = {
    ActionType.CODE: "key",
    ActionType.PAGEVIEW: "urlMatch",
    ActionType.CLICK: "cssSelector",
}


@login_required
@require_http_methods(["GET", "POST"])
def actions_page(request):
    """``/<org_slug>/settings/actions`` — list + create + update + delete ActionClasses.

    Workspace-scoped RBAC: read to view, write to mutate. A caller without read access to the
    current workspace sees an empty list (default-deny, not a 403, mirroring the API list); a
    caller without write access gets a 403 on a mutating POST.
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    environment = _target_environment(request)
    can_view = environment is not None and can(request.user, "view_action_class", environment)

    if request.method == "POST":
        return _handle_post(request, org, environment)

    actions = []
    if can_view:
        actions = list(
            ActionClass.objects.filter(environment=environment).order_by("name", "id")
        )

    can_write = environment is not None and can(
        request.user, "create_action_class", environment
    )

    return render(
        request,
        "settings/actions.html",
        {
            "org": org,
            "section": "actions",
            "actions": actions,
            "environment": environment,
            "type_choices": ActionType.choices,  # [(value, label), ...]
            "can_write": can_write,
            "no_workspace": environment is None,
            "config_form_field": _CONFIG_FORM_FIELD,
            "error": request.session.pop("actions:error", None),
        },
    )


def _handle_post(request, org, environment):
    """Create / update / delete dispatch (form ``op`` field). Redirects (post-redirect-get)."""
    op = request.POST.get("op")
    redirect_url = f"/{org.slug}/settings/actions"

    # Mutations require workspace write on the target environment.
    if environment is None or not can(request.user, "create_action_class", environment):
        return render(
            request,
            "organizations/not_a_member.html",
            {"org": org},
            status=403,
        )

    if op == "create":
        name = (request.POST.get("name") or "").strip()
        action_type = request.POST.get("type") or ActionType.CODE
        description = (request.POST.get("description") or "").strip()
        config = _config_from_form(request, action_type)
        if name:
            try:
                services.create_action_class(
                    environment=environment,
                    name=name,
                    action_type=action_type,
                    description=description,
                    config=config,
                    created_by_user_id=request.user.id,
                )
            except services.ActionConfigError as exc:
                request.session["actions:error"] = _format_config_error(exc)
            except Exception:  # IntegrityError on the unique-name constraint → friendly message.
                request.session["actions:error"] = (
                    f"An action named “{name}” already exists in this environment."
                )
        return redirect(redirect_url)

    if op == "update":
        action = _get_scoped_action(request, environment)
        if action is not None:
            name = (request.POST.get("name") or "").strip() or action.name
            action_type = request.POST.get("type") or action.type
            description = (request.POST.get("description") or "").strip()
            config = _config_from_form(request, action_type)
            try:
                services.update_action_class(
                    action_class=action,
                    name=name,
                    action_type=action_type,
                    description=description,
                    config=config,
                )
            except services.ActionConfigError as exc:
                request.session["actions:error"] = _format_config_error(exc)
            except Exception:
                request.session["actions:error"] = (
                    f"An action named “{name}” already exists in this environment."
                )
        return redirect(redirect_url)

    if op == "delete":
        action = _get_scoped_action(request, environment)
        if action is not None:
            services.delete_action_class(action_class=action)
        return redirect(redirect_url)

    return redirect(redirect_url)


@login_required
@require_http_methods(["GET"])
def connection_page(request):
    """``/<org_slug>/settings/connection`` — the Website & App Connection setup page.

    Shows the current workspace environment's id + a copy-able embed snippet (the SHARED widget
    contract) + setup steps. Read-only (no mutation): viewing needs workspace ``read`` (any member
    with workspace access). The snippet host (``app_origin``) is derived from the request so it
    matches whatever scheme/host the admin is on.
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    environment = _target_environment(request)
    can_view = environment is not None and can(request.user, "view_action_class", environment)

    # The widget script origin: scheme://host (no trailing slash). The widget's api host defaults
    # to the script's own origin, so this is the only host value the snippet needs.
    app_origin = request.build_absolute_uri("/").rstrip("/")
    env_id = str(environment.id) if environment is not None else ""
    org_slug = org.slug

    # THE SHARED CONTRACT snippet (must match the widget): a single <script> tag pointing at the
    # widget bundle. data-org-slug is REQUIRED because the public widget APIs are tenant-prefixed
    # (/api/v1/<org_slug>/public/...); data-environment-id selects the environment. The widget's
    # api host defaults to the script's own origin.
    embed_snippet = (
        f'<script src="{app_origin}/static/widget/opensurvey-widget.js" '
        f'data-environment-id="{env_id}" '
        f'data-org-slug="{org_slug}"></script>'
    )

    return render(
        request,
        "settings/connection.html",
        {
            "org": org,
            "section": "connection",
            "environment": environment if can_view else None,
            "env_id": env_id if can_view else "",
            "app_origin": app_origin,
            "embed_snippet": embed_snippet if can_view else "",
            "no_workspace": environment is None or not can_view,
        },
    )


# --- helpers ----------------------------------------------------------------------------- #
def _config_from_form(request, action_type) -> dict:
    """Build the type-specific config dict from the single ``config_value`` form field.

    Each type stores one optional string under its own key (code→key, pageview→urlMatch,
    click→cssSelector). A blank value yields an empty config (valid for every type)."""
    value = (request.POST.get(_CONFIG_FORM_FIELD) or "").strip()
    if not value:
        return {}
    key = _CONFIG_KEY_BY_TYPE.get(action_type)
    if key is None:
        return {}
    return {key: value}


def _format_config_error(exc: services.ActionConfigError) -> str:
    """Flatten an ActionConfigError into a single human message for the page banner."""
    parts = [f"{e['field']}: {e['message']}" for e in exc.errors]
    return "Invalid action configuration. " + "; ".join(parts)


def _get_scoped_action(request, environment) -> ActionClass | None:
    """Resolve the form's action, scoped to the active environment (defense in depth)."""
    action = ActionClass.objects.filter(pk=request.POST.get("action_id")).first()
    if action is None:
        return None
    if environment is not None and action.environment_id != environment.id:
        return None
    return action


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
