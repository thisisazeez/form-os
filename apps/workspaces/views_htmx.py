"""
Server-rendered (HTMX) Workspace settings — the browser counterpart of the Workspace JSON API
(apps.workspaces.views). Renders the General settings of a single workspace (name/slug, default
branding color, environments) + a danger zone, matching designs/stitch/workspace-settings.png.

Pages (TENANT schema, config/urls.py under /<org_slug>/):
  /<org_slug>/settings/workspace            GET/POST  — the DEFAULT workspace (first by name).
  /<org_slug>/workspaces/<uuid:id>/settings GET/POST  — an addressable workspace.

The name-rename is wired to apps.workspaces.services.update_workspace (the SAME service the
DRF PATCH uses — no business logic here). The slug is immutable (services enforce); branding
color + environments are read-only displays in v1 (no Workspace branding column yet — the
color picker reflects the survey-styling default and is presentational).

RBAC (mirrors apps.surveys.builder_views.survey_builder_host + apps.common.permissions.can):
  - can(view_workspace) is the visibility gate: a caller who cannot even view the workspace
    gets 404 (no existence oracle).
  - can(manage_workspace) gates the page + the rename: a caller who can view but not manage
    gets 403. Org owner/admin get implicit manage (rbac.md §3); a team with workspace `manage`
    also qualifies.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name

from apps.common.permissions import can
from apps.workspaces import services
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace


@login_required
@require_http_methods(["GET", "POST"])
def create_workspace_page(request):
    """``/<org_slug>/workspaces/new`` — create a workspace (+ prod/dev envs), then open it.

    The browser counterpart of ``POST /api/v1/<org>/workspaces`` (same ``services.create_workspace``).
    Gated by ``can(create_workspace)`` (org owner/admin — rbac.md §6); a member without that role
    gets 403. On success redirects to the org dashboard.
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")
    if not can(request.user, "create_workspace"):
        raise PermissionDenied()

    name_error = None
    if request.method == "POST":
        try:
            services.create_workspace(
                name=request.POST.get("name", ""),
                slug=None,
                created_by_user_id=request.user.id,
            )
        except ValidationError as exc:
            name_error = exc.messages[0] if exc.messages else "Invalid name."
        else:
            # request.path is slug-stripped by TenantSlugMiddleware — build the slug-prefixed
            # dashboard URL so the post-create redirect lands inside the org (not a public 404).
            return redirect(f"/{org.slug}/")

    return render(
        request, "workspaces/create_workspace.html", {"org": org, "name_error": name_error}
    )


@login_required
@require_http_methods(["POST"])
def switch_workspace(request, workspace_id):
    """``POST /<org_slug>/switch-workspace/<id>`` — set the caller's current workspace, then
    return to the dashboard. Only a workspace the caller can READ is accepted (RBAC); the whole
    admin app (dashboard/surveys/results/contacts/segments) then scopes to it."""
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    from apps.common.active_workspace import set_current_workspace
    from apps.workspaces.access import workspace_access_map

    if workspace_id not in workspace_access_map(request.user):
        raise PermissionDenied()
    workspace = Workspace.objects.filter(pk=workspace_id).first()
    if workspace is None:
        raise Http404("Workspace not found.")
    set_current_workspace(request, workspace)
    return redirect(f"/{org.slug}/")


@login_required
@require_http_methods(["GET", "POST"])
def workspace_settings_page(request, workspace_id=None):
    """``/<org_slug>/settings/workspace`` (default ws) or ``/.../workspaces/<id>/settings``."""
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    workspace = _resolve_workspace(workspace_id)
    if workspace is None:
        raise Http404("Workspace not found.")

    # 404 if the caller can't even see it (no oracle); 403 if they can view but not manage.
    if not can(request.user, "view_workspace", workspace):
        raise Http404()
    if not can(request.user, "manage_workspace", workspace):
        raise PermissionDenied()

    name_error = None
    general_error = None
    branding_saved = False
    if request.method == "POST":
        # Two forms post to this page (both gated by manage_workspace above), distinguished by a
        # hidden `section` field so each can succeed/fail independently:
        #   - "branding": the white-label "remove branding" toggle (services.update_workspace_general).
        #   - "general"  (default): the rename (services.update_workspace) + the recontact cooldown
        #     (services.update_workspace_general). The rename uses the SAME service the DRF PATCH
        #     uses; the slug stays immutable.
        section = request.POST.get("section", "general")
        if section == "branding":
            # Checkbox: present == on. Always provided (the form submits the section), so passing the
            # bool unconditionally is correct (no "leave untouched" ambiguity for this surface).
            services.update_workspace_general(
                workspace=workspace,
                recontact_days=workspace.recontact_days,  # unchanged by this form
                remove_branding=bool(request.POST.get("remove_branding")),
            )
            branding_saved = True
        else:
            # The rename + recontact save are ONE atomic unit: if the recontact value is invalid,
            # the rename rolls back too (no half-applied form — the user sees the error with the
            # original name still in place, not a silently-committed rename).
            try:
                with transaction.atomic():
                    services.update_workspace(
                        workspace=workspace, name=request.POST.get("name", "")
                    )
                    services.update_workspace_general(
                        workspace=workspace,
                        recontact_days=request.POST.get("recontact_days", ""),
                    )
            except ValidationError as exc:
                # Distinguish which field failed so the right banner shows. update_workspace raises
                # {"name": ...}; update_workspace_general raises {"recontact_days": ...}.
                field = (
                    next(iter(exc.message_dict)) if hasattr(exc, "message_dict") else None
                )
                message = exc.messages[0] if exc.messages else "Invalid value."
                if field == "name":
                    name_error = message
                else:
                    general_error = message
                # The atomic block rolled back; the refresh below reloads the persisted (unchanged)
                # values so the re-rendered form shows them, not the rejected input.
            else:
                # request.path is slug-stripped by TenantSlugMiddleware — re-prepend the org
                # slug so the post-save redirect lands back on the real (slug-prefixed) URL.
                return redirect(f"/{request.tenant.slug}{request.path}")
        # Non-redirecting path (branding save, or a rolled-back general error): reload the
        # persisted config (incl. name, which may have been rolled back) for the render.
        workspace.refresh_from_db(fields=["name", "recontact_days", "remove_branding"])

    environments = list(
        Environment.objects.filter(workspace=workspace).order_by("type")
    )
    has_production = any(e.type == EnvType.PRODUCTION for e in environments)
    has_development = any(e.type == EnvType.DEVELOPMENT for e in environments)

    return render(
        request,
        "workspaces/settings.html",
        {
            "org": org,
            "workspace": workspace,
            "environments": environments,
            "has_production": has_production,
            "has_development": has_development,
            "name_error": name_error,
            # General config (recontact cooldown) + its validation error, if any.
            "recontact_days": workspace.recontact_days,
            "general_error": general_error,
            # White-label toggle (enterprise) + a one-shot "saved" flash for the branding form.
            "remove_branding": workspace.remove_branding,
            "branding_saved": branding_saved,
            # The project Look & Feel theme (used by the Branding section's swatch + entry point).
            "theme": workspace.survey_theme or {},
            # The danger-zone delete is org-owner/admin only (delete_workspace, rbac.md §6).
            "can_delete": can(request.user, "delete_workspace", workspace),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def workspace_look_and_feel_page(request, workspace_id=None):
    """``/<org_slug>/settings/workspace/look-and-feel`` (default ws) or
    ``/.../workspaces/<id>/settings/look-and-feel`` — the project-level "Look & Feel" theme.

    This is the workspace counterpart of the per-survey Styling tab: the theme set here is the
    DEFAULT styling for every survey in the workspace (merged UNDER each survey's own styling at
    serve time — apps.workspaces.theme.merge_styling). Same RBAC layering as
    ``workspace_settings_page``: ``view_workspace`` gates visibility (404, no oracle),
    ``manage_workspace`` gates the page + the save (403). The save runs through
    ``services.update_workspace_theme`` (validates against #/$defs/styling)."""
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    workspace = _resolve_workspace(workspace_id)
    if workspace is None:
        raise Http404("Workspace not found.")

    if not can(request.user, "view_workspace", workspace):
        raise Http404()
    if not can(request.user, "manage_workspace", workspace):
        raise PermissionDenied()

    theme_error = None
    theme_saved = False
    if request.method == "POST":
        try:
            services.update_workspace_theme(
                workspace=workspace, theme=_theme_from_post(request.POST)
            )
        except ValidationError as exc:
            # update_workspace_theme raises ValidationError({"survey_theme": [...]}) on a bad
            # shape; surface the first message (the form re-renders the submitted values).
            messages = exc.message_dict.get("survey_theme") if hasattr(exc, "message_dict") else None
            theme_error = (messages[0] if messages else "Invalid theme.") if messages else (
                exc.messages[0] if exc.messages else "Invalid theme."
            )
        else:
            theme_saved = True
            workspace.refresh_from_db(fields=["survey_theme"])

    theme = workspace.survey_theme or {}
    return render(
        request,
        "workspaces/look_and_feel.html",
        {
            "org": org,
            "workspace": workspace,
            "theme": theme,
            "theme_error": theme_error,
            "theme_saved": theme_saved,
        },
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _theme_from_post(post) -> dict:
    """Build a #/$defs/styling theme dict from the Look & Feel form POST.

    Only keys the user actually set are included — an empty field means "not set" (so it falls
    back to runtime defaults / a parent), NOT an empty string written into the theme. ``roundness``
    is coerced to a number when present; the checkbox maps to a bool; ``background`` is assembled
    from its two sub-inputs only when a type is chosen. The result is validated downstream by
    ``services.update_workspace_theme`` (we do NOT pre-validate here — one source of truth)."""
    theme: dict = {}
    for key in (
        "brandColor",
        "questionColor",
        "backgroundColor",
        "buttonColor",
        "buttonTextColor",
        "logoUrl",
    ):
        value = (post.get(key) or "").strip()
        if value:
            theme[key] = value

    roundness = (post.get("roundness") or "").strip()
    if roundness:
        try:
            theme["roundness"] = float(roundness) if "." in roundness else int(roundness)
        except ValueError:
            # Leave it as the raw string → the schema validator rejects it with a clear message.
            theme["roundness"] = roundness

    card = (post.get("cardArrangement") or "").strip()
    if card:
        theme["cardArrangement"] = card

    # hideProgressBar is a checkbox: present == True. Only emit when checked (absent == default).
    if post.get("hideProgressBar"):
        theme["hideProgressBar"] = True

    bg_type = (post.get("backgroundType") or "").strip()
    if bg_type:
        bg: dict = {"type": bg_type}
        bg_value = (post.get("backgroundValue") or "").strip()
        if bg_value:
            bg["value"] = bg_value
        theme["background"] = bg

    return theme


def _resolve_workspace(workspace_id):
    """Resolve the target Workspace: the id when addressed, else the first by name (default)."""
    if workspace_id is not None:
        try:
            return Workspace.objects.get(pk=workspace_id)
        except (Workspace.DoesNotExist, ValueError, ValidationError):
            return None
    return Workspace.objects.order_by("name", "id").first()


def _routed_org(request):
    org = getattr(request, "tenant", None)
    if org is None or getattr(org, "schema_name", None) == get_public_schema_name():
        return None
    return org
