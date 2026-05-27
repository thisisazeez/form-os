"""
Server-rendered (HTMX) Segment-builder admin pages — the browser counterpart of the Segments
JSON API (apps/segments/views.py). Mirrors the canonical wired-view pattern in
``apps/organizations/views_htmx.py::members_page``: thin Django HTML views (NOT DRF) delegating
ALL business logic to ``apps.segments.services`` (the same layer the API uses) and gating every
action through ``apps.common.permissions.can`` (STYLE_GUIDE §e / rbac.md §7).

Pages (TENANT schema; mounted by config/urls.py under ``/<org_slug>/`` — slug stripped by
TenantSlugMiddleware, ADR-001):
  GET  /<org_slug>/segments              → segments_page    (list of saved segments)
  GET  /<org_slug>/segments/new          → segment_builder  (new-segment builder)
  POST /<org_slug>/segments/new          → segment_builder  (create via services.create_segment)
  GET  /<org_slug>/segments/<id>         → segment_builder  (edit-segment builder)
  POST /<org_slug>/segments/<id>         → segment_builder  (replace via services.replace_segment)
  POST /<org_slug>/segments/preview      → segment_preview  (HTMX live count + sample list)

``segment_builder`` is one view per URL with a GET/POST method branch (mirrors
``members_page`` / ``create_organization_page``) — Django dispatches by path, not method, so a
single view handles both the builder render (GET) and the save (POST).

ENVIRONMENT RESOLUTION: same single rule as the contacts pages — reused from
``apps.contacts.views_htmx._resolve_environment`` so the builder scopes to the same environment
the contacts list was reached from (the data layer scopes contacts + segments to an
Environment; the routes are org-level).

The builder posts the filter JSON it assembles (in Alpine) to ``segment_preview`` on every
change (HTMX, debounced); the server VALIDATES + PREVIEWS via ``services.preview`` and returns
the live matching-count + a sample contact list partial. Save posts ``title`` + ``filter``
(JSON) to ``segment_save`` which calls the create/replace service. RBAC: ``create_segment`` /
``edit_segment`` (workspace ``write``); a read-only caller gets 403 on save (matches
SegmentDetailView's 403-on-mutation precedent).
"""
from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.common.permissions import active_org_membership, can
from apps.contacts.models import ContactAttributeKey
from apps.contacts.scoping import environment_access_map, pii_unmasked_for_environment
from apps.contacts.views_htmx import (
    attr_value_map,
    display_name,
    initials,
    resolve_environment,
    segment_rail,
)
from apps.segments import filters as segment_filters
from apps.segments import services
from apps.segments.models import Segment

# Sample contacts shown in the builder's live preview rail (the full list is browsable via the
# contacts page). Keep small — the preview is a glance, not a table.
PREVIEW_SAMPLE_SIZE = 8
# Cap on contacts scanned for the live count (mirrors the API DEFAULT_EVAL_LIMIT). A count at the
# cap renders as "N+". Exact large-scale counts are a future SQL-compiler job (FOLLOWUPS).
PREVIEW_COUNT_LIMIT = 1000

# Operator option lists per attribute data_type, derived from the FROZEN operator-validity table
# in apps.segments.filters (single source of truth) — NEVER hardcoded here. The builder shows
# only the operators valid for the chosen attribute's type, and the same table the service
# validates against. ``needs_value`` drives whether the value input is shown.
_NO_VALUE = segment_filters._NO_VALUE_OPS
_HUMAN_OP = {
    "equals": "is",
    "notEquals": "is not",
    "contains": "contains",
    "doesNotContain": "does not contain",
    "startsWith": "starts with",
    "endsWith": "ends with",
    "isOneOf": "is one of",
    "greaterThan": "greater than",
    "greaterThanOrEqual": "greater or equal",
    "lessThan": "less than",
    "lessThanOrEqual": "less or equal",
    "before": "before",
    "after": "after",
    "isAccepted": "is accepted",
    "isNotAccepted": "is not accepted",
    "isSubmitted": "is submitted",
    "isSkipped": "is skipped",
    "isEmpty": "is empty",
    "isNotEmpty": "is not empty",
}


def _operators_by_type() -> dict[str, list[dict]]:
    """``{data_type: [{op, label, needs_value, is_array}]}`` straight from the frozen filters
    table — the builder's operator dropdowns are generated from this, never hardcoded."""
    out: dict[str, list[dict]] = {}
    for data_type, ops in segment_filters.VALID_OPERATORS_BY_TYPE.items():
        items = []
        for op in sorted(ops):
            items.append(
                {
                    "op": op,
                    "label": _HUMAN_OP.get(op, op),
                    "needs_value": op not in _NO_VALUE,
                    "is_array": op in segment_filters._ARRAY_VALUE_OPS,
                }
            )
        out[data_type] = items
    return out


def _attribute_keys(environment) -> list[dict]:
    """The env's attribute keys for the builder's attribute dropdown: ``[{key, label, data_type}]``."""
    return [
        {"key": k, "label": label or k, "data_type": dtype}
        for k, label, dtype in ContactAttributeKey.objects.filter(environment=environment)
        .order_by("key")
        .values_list("key", "label", "data_type")
    ]


def _is_org_member(request) -> bool:
    return active_org_membership(request.user) is not None


# ---------------------------------------------------------------------------
# Pages.
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET"])
def segments_page(request):
    """``/<org_slug>/segments`` — the list of saved segments with their matching counts (the
    saved-segments rail surfaced as a full page; New segment opens the builder)."""
    environment, access_map = resolve_environment(request)
    if environment is None:
        if not _is_org_member(request):
            raise PermissionDenied("You are not a member of this organization.")
        return render(request, "segments/list.html", {"environment": None, "segments": []})

    if not can(request.user, "view_segment", environment):
        raise Http404("Not found.")

    can_edit = can(request.user, "create_segment", environment)
    segments = segment_rail(environment)
    return render(
        request,
        "segments/list.html",
        {"environment": environment, "segments": segments, "can_edit": can_edit},
    )


@login_required
@require_http_methods(["GET", "POST"])
def segment_builder(request, segment_id=None):
    """``/<org_slug>/segments/new`` (create) or ``/<org_slug>/segments/<id>`` (edit) — the
    visual filter-group builder + live preview rail.

    One view per URL with a method branch (mirrors members_page / create_organization_page):
    GET renders the builder; POST saves via the service.
    """
    if request.method == "POST":
        return _do_save(request, segment_id)
    return _render_builder(request, segment_id)


def _render_builder(request, segment_id):
    if segment_id is not None:
        segment = _get_segment_or_404(segment_id)
        if not can(request.user, "view_segment", segment):
            raise Http404("Segment not found.")  # no oracle.
        # Editing needs workspace write — a viewer-only caller gets 403 (matches the API).
        if not can(request.user, "edit_segment", segment):
            raise PermissionDenied("You do not have permission to edit this segment.")
        environment = segment.environment
        initial_title = segment.title
        initial_filter = segment.filter or {}
        post_url = _segment_url(request, segment.id)
    else:
        environment, access_map = resolve_environment(request)
        if environment is None:
            if not _is_org_member(request):
                raise PermissionDenied("You are not a member of this organization.")
            # No environment to build a segment in → bounce to the (empty) segments list.
            return redirect(_segments_url(request))
        if not can(request.user, "create_segment", environment):
            # Can the caller even view segments here? If yes → 403; if not → 404 (no oracle).
            if can(request.user, "view_segment", environment):
                raise PermissionDenied("You do not have permission to create segments here.")
            raise Http404("Not found.")
        segment = None
        initial_title = ""
        # PREFILL: the contacts "Save as segment" pivot (apps/contacts/views_htmx) redirects here
        # with ``?filter=<json>`` — a ready-made attribute-filter tree (e.g. email isOneOf the
        # selected contacts). Hydrate the builder with it so the user only names + saves. A
        # malformed/oversized filter is ignored (the builder falls back to an empty tree); the
        # save path still validates via services.create_segment, so this is display-only.
        initial_filter = _prefill_filter_from_query(request)
        post_url = _segment_new_url(request)

    return render(
        request,
        "segments/builder.html",
        _builder_context(
            environment=environment,
            segment=segment,
            initial_title=initial_title,
            initial_filter=initial_filter,
            post_url=post_url,
            request=request,
        ),
    )


def _do_save(request, segment_id):
    """Create (``/segments/new``) or replace (``/segments/<id>``) a segment via the service.

    The builder posts ``title`` + ``filter`` (a JSON string assembled in Alpine) + a hidden
    ``environment_id`` (so a create save lands in the env the builder was opened against — the
    POST has no query string to carry ``?environment=``). On a filter/title error we re-render
    the builder with the messages + the submitted state preserved. Success → redirect to list.
    """
    title = (request.POST.get("title") or "").strip()
    filter_raw = request.POST.get("filter") or "{}"
    try:
        filter_def = json.loads(filter_raw)
        if not isinstance(filter_def, dict):
            raise ValueError("filter must be a JSON object")
    except (ValueError, TypeError):
        filter_def = {}
        errors = ["The filter could not be read. Please rebuild the conditions."]
        return _rerender_builder(request, segment_id, title, filter_def, errors)

    if segment_id is not None:
        segment = _get_segment_or_404(segment_id)
        if not can(request.user, "edit_segment", segment):
            raise PermissionDenied("You do not have permission to edit this segment.")
        try:
            services.replace_segment(segment=segment, title=title, filter_def=filter_def)
        except services.SegmentFilterError as exc:
            return _rerender_builder(request, segment_id, title, filter_def, exc.schema_errors)
        except services.SegmentTitleTakenError as exc:
            return _rerender_builder(request, segment_id, title, filter_def, [str(exc)])
        except ValueError as exc:
            return _rerender_builder(request, segment_id, title, filter_def, [str(exc)])
        return redirect(_segments_url(request))

    # Create — resolve the environment from the hidden field (falling back to the default rule).
    environment = _environment_from_post(request)
    if environment is None or not can(request.user, "create_segment", environment):
        raise PermissionDenied("You do not have permission to create segments here.")
    try:
        services.create_segment(
            environment=environment,
            title=title,
            filter_def=filter_def,
            created_by_user_id=request.user.id,
        )
    except services.SegmentFilterError as exc:
        return _rerender_builder(request, None, title, filter_def, exc.schema_errors)
    except services.SegmentTitleTakenError as exc:
        return _rerender_builder(request, None, title, filter_def, [str(exc)])
    except ValueError as exc:
        return _rerender_builder(request, None, title, filter_def, [str(exc)])
    return redirect(_segments_url(request))


def _environment_from_post(request):
    """Resolve the create-target environment from the hidden ``environment_id`` POST field,
    validating it's one the caller can read; falls back to the default-resolution rule. The POST
    carries no query string, so ``?environment=`` can't be relied on here."""
    posted = request.POST.get("environment_id")
    access_map = environment_access_map(request.user)
    if posted and posted in {str(env_id) for env_id in access_map}:
        from apps.workspaces.models import Environment

        env = Environment.objects.select_related("workspace").filter(pk=posted).first()
        if env is not None:
            return env
    environment, _ = resolve_environment(request)
    return environment


@login_required
@require_http_methods(["POST"])
def segment_preview(request):
    """``POST /<org_slug>/segments/preview`` — HTMX live preview for the builder. Takes the
    in-progress ``filter`` JSON, VALIDATES + PREVIEWS via ``services.preview`` against the
    resolved environment, and returns the count + a sample contact list partial.

    Invalid filter → the same partial with the validation messages (so the builder shows why
    the count can't update yet). Never raises a 500 for a malformed filter."""
    environment = _environment_from_post(request)
    if environment is None or not can(request.user, "view_segment", environment):
        # No env / no visibility → an empty preview (the builder shouldn't have loaded anyway).
        return render(
            request,
            "segments/_preview.html",
            {"count": 0, "results": [], "errors": [], "environment": None},
        )

    filter_raw = request.POST.get("filter") or "{}"
    try:
        filter_def = json.loads(filter_raw)
        if not isinstance(filter_def, dict):
            raise ValueError
    except (ValueError, TypeError):
        return render(
            request,
            "segments/_preview.html",
            {
                "count": None,
                "results": [],
                "errors": ["Add a condition to preview matching contacts."],
                "environment": environment,
            },
        )

    try:
        matched = services.preview(
            filter_def, environment=environment, limit=PREVIEW_COUNT_LIMIT
        )
    except services.SegmentFilterError as exc:
        return render(
            request,
            "segments/_preview.html",
            {"count": None, "results": [], "errors": exc.schema_errors, "environment": environment},
        )

    unmasked = pii_unmasked_for_environment(request.user, environment)
    sample = []
    for contact in matched[:PREVIEW_SAMPLE_SIZE]:
        amap = attr_value_map(contact, unmasked=unmasked)
        name = display_name(amap, contact)
        sample.append(
            {
                "id": contact.id,
                "name": name,
                "initials": initials(name),
                "email": amap.get("email", {}).get("value", ""),
            }
        )
    count = len(matched)
    return render(
        request,
        "segments/_preview.html",
        {
            "count": count,
            "count_capped": count >= PREVIEW_COUNT_LIMIT,
            "results": sample,
            "more": max(count - len(sample), 0),
            "errors": [],
            "environment": environment,
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------
def _prefill_filter_from_query(request) -> dict:
    """Read a ``?filter=<json>`` querystring (the contacts "Save as segment" pivot) into a filter
    dict for the builder to hydrate from. Returns ``{}`` (empty/match-all) on anything malformed
    or oversized — display-only; the save path is the authoritative validator."""
    raw = request.GET.get("filter")
    if not raw or len(raw) > 100_000:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    # Cheap bounds check so a pathological tree can't blow up the builder render.
    try:
        segment_filters.check_filter_bounds(parsed)
    except segment_filters.FilterValidationError:
        return {}
    return parsed


def _get_segment_or_404(segment_id) -> Segment:
    try:
        return Segment.objects.select_related("environment__workspace").get(pk=segment_id)
    except (Segment.DoesNotExist, ValueError):
        raise Http404("Segment not found.") from None


def _builder_context(*, environment, segment, initial_title, initial_filter, post_url, request, save_errors=None):
    """Assemble the builder template context (shared by the GET render + the post-error
    re-render so the two never drift). The builder's Alpine state hydrates from these objects
    via Django's ``json_script`` filter (safe JSON-in-HTML; the template embeds them as
    ``<script type="application/json">`` blocks and JSON.parses them)."""
    keys = _attribute_keys(environment) if environment else []
    return {
        "environment": environment,
        "segment": segment,
        "initial_title": initial_title,
        # Raw objects for json_script (the template renders them as <script application/json>).
        "initial_filter": initial_filter,
        "attribute_keys": keys,
        "operators_by_type": _operators_by_type(),
        "post_url": post_url,
        "preview_url": _preview_url(request),
        "cancel_url": _segments_url(request),
        "save_errors": list(save_errors or []),
    }


def _rerender_builder(request, segment_id, title, filter_def, errors):
    """Re-render the builder after a failed save, preserving the submitted title + filter and
    showing the error messages."""
    if segment_id is not None:
        segment = _get_segment_or_404(segment_id)
        environment = segment.environment
        post_url = _segment_url(request, segment.id)
    else:
        segment = None
        environment = _environment_from_post(request)
        post_url = _segment_new_url(request)
    return render(
        request,
        "segments/builder.html",
        _builder_context(
            environment=environment,
            segment=segment,
            initial_title=title,
            initial_filter=filter_def,
            post_url=post_url,
            request=request,
            save_errors=errors,
        ),
    )


# --- URL helpers (org-prefixed; slug from the routed tenant) --------------------------------
def _slug(request):
    return getattr(getattr(request, "tenant", None), "slug", None)


def _segments_url(request) -> str:
    s = _slug(request)
    return f"/{s}/segments" if s else "/"


def _segment_new_url(request) -> str:
    s = _slug(request)
    return f"/{s}/segments/new" if s else "/"


def _segment_url(request, segment_id) -> str:
    s = _slug(request)
    return f"/{s}/segments/{segment_id}" if s else "/"


def _preview_url(request) -> str:
    s = _slug(request)
    return f"/{s}/segments/preview" if s else "/"
