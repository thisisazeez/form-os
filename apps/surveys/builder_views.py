"""
Authenticated survey-builder HOST page (Wave 3b).

This is the thin Django half of the survey-builder React island: an AUTHENTICATED,
RBAC-gated admin page that mounts the builder island (django-vite) and hands it the survey's
full definition so it can hydrate without a round-trip. It does NOT touch the authenticated
Survey CRUD API (apps/surveys/views.py) — the island calls that API; this view only renders
the host page and gates access on the same single authorization entry point, ``can()``.

  GET /{org_slug}/surveys/{survey_id}/edit  → survey_builder_host
        (tenant HTML family; the org slug is stripped by TenantSlugMiddleware before the
         tenant urlconf matches ``surveys/<uuid:survey_id>/edit`` — ADR-001, config/urls.py)

GATING (login + RBAC), layered exactly like ``SurveyDetailView.get`` so there is no behaviour
drift between the API and this page (rbac.md §7 — one authorization entry point, every
surface calls it):
  1. ``@login_required`` — anonymous → redirect to LOGIN_URL (Django HTML view, NOT DRF).
  2. Load the survey or 404 (unknown id / not in this tenant).
  3. ``can(user, "view_survey", survey)`` is False → 404 (NO existence oracle: a survey the
     caller cannot even see is indistinguishable from a missing one — mirrors the detail GET).
  4. ``can(user, "edit_survey", survey)`` is False → 403 (the caller can see the survey but
     lacks workspace ``write`` — e.g. an org member with only ``read``).

MOUNT CONTRACT (PRE-AGREED / FIXED — the frontend builds to exactly this; mirrors the runtime
host's data-attr + django-vite pattern in apps/responses/public_views.py::public_runtime_host):
  <div id="opensurvey-builder"
       data-survey-id="…"            the survey UUID
       data-org-slug="…"             the routed org slug
       data-api-base="/api/v1/{org_slug}"   the AUTHENTICATED Survey CRUD base (NOT /public)
       data-initial-survey="…JSON…"  the survey's full definition (openapi ``Survey`` shape,
                                     via SurveySerializer) so the island hydrates offline.
  {% vite_asset "src/islands/builder/main.tsx" %}  gated on vite_built (manifest present),
  exactly like the runtime host page.
"""
from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from apps.actions.models import ActionClass
from apps.actions.serializers import ActionClassSerializer
from apps.common.permissions import can

from .models import Survey
from .serializers import SurveySerializer


def _get_survey_or_404(survey_id) -> Survey:
    """Load a survey (with its workspace chain for ``can()``) or 404 (mirrors views.py)."""
    try:
        return Survey.objects.select_related(
            "environment", "environment__workspace"
        ).get(pk=survey_id)
    except (Survey.DoesNotExist, ValueError) as exc:
        raise Http404("Survey not found.") from exc


@login_required
@require_http_methods(["GET"])
def survey_builder_host(request, survey_id):
    """Render the authenticated builder host page that mounts the React builder island.

    Tenant HTML family: the org slug was stripped by TenantSlugMiddleware, so this view runs
    with the org's schema active and ``connection.tenant`` is the routed org. Authentication
    is enforced by ``@login_required`` (anonymous → LOGIN_URL); authorization delegates to
    ``can()`` — ``view_survey`` gates visibility (→ 404, no oracle) and ``edit_survey`` gates
    the edit page itself (→ 403). See the module docstring for the full layering.
    """
    survey = _get_survey_or_404(survey_id)

    # Visibility first: a survey the caller cannot read is treated as not found (no existence
    # oracle), exactly like SurveyDetailView.get (rbac.md NotFound semantics).
    if not can(request.user, "view_survey", survey):
        raise Http404("Survey not found.")

    # Edit gate: the caller can see it but editing the definition needs workspace `write`.
    if not can(request.user, "edit_survey", survey):
        raise PermissionDenied("You do not have permission to edit this survey.")

    tenant = getattr(connection, "tenant", None)
    org_slug = getattr(tenant, "slug", None)

    vite_conf = settings.DJANGO_VITE.get("default", {})
    manifest_path = vite_conf.get("manifest_path")
    vite_built = bool(vite_conf.get("dev_mode")) or (
        manifest_path is not None and manifest_path.exists()
    )

    # The AUTHENTICATED Survey CRUD base the island calls (GET/PUT survey) — NOT the /public
    # runtime base. ROOT only: the builder island (frontend/.../builder/api.ts) appends
    # /{orgSlug}/surveys/... itself, so org-prefixing here double-prefixes -> 404 on save/load.
    api_base = "/api/v1"

    # The survey's full definition (openapi ``Survey`` shape). Serialized to a JSON string and
    # rendered as the data-initial-survey attribute; Django's attribute auto-escaping is the
    # correct escaping (the browser un-escapes via el.dataset.initialSurvey, then JSON.parse).
    # default=str handles UUID/datetime fields the serializer surfaces.
    #
    # IMPORTANT — DO NOT bake the project theme into this styling. ``initial_survey`` carries the
    # survey's RAW ``styling`` (what the builder PUTs back via SurveyWriteSerializer). Merging the
    # project theme in HERE would persist it as the survey's own styling on the next save and
    # defeat inheritance forever. The project theme is handed to the island SEPARATELY (below) so
    # a preview can compose them without any round-trip risk.
    initial_survey_json = json.dumps(SurveySerializer(survey).data, default=str)

    # The workspace's project-level "Look & Feel" theme — same #/$defs/styling shape — passed as a
    # SEPARATE attribute (data-project-theme) so the builder can preview the inherited default
    # UNDER the survey styling (apps.workspaces.theme.merge_styling: survey keys win). {} when the
    # workspace has no project theme. The workspace chain is select_related above (no extra query).
    project_theme = getattr(survey.environment.workspace, "survey_theme", None) or {}
    project_theme_json = json.dumps(project_theme, default=str)

    # The survey environment's ActionClasses (FEATURE_PARITY WS-11) — handed to the island as a
    # SEPARATE attribute (data-action-classes) so the Settings tab's app-survey trigger picker can
    # render human names without a fetch on mount. The ActionClassSerializer read shape ({id, name,
    # type, ...}); the builder only reads id/name/type. [] when the env has no actions yet (the
    # picker then shows the "create an action first" hint). Tenant schema is active here, so this
    # is a TENANT-schema query, matching the survey's environment.
    action_classes = ActionClass.objects.filter(environment=survey.environment).order_by(
        "name", "id"
    )
    action_classes_json = json.dumps(
        ActionClassSerializer(action_classes, many=True).data, default=str
    )

    context = {
        "survey_id": str(survey.id),
        "survey_name": survey.name,
        "org_slug": org_slug,
        "api_base": api_base,
        "initial_survey_json": initial_survey_json,
        "project_theme_json": project_theme_json,
        "action_classes_json": action_classes_json,
        "vite_built": vite_built,
    }
    return render(request, "builder/edit.html", context)
