"""
Server-rendered (HTMX) Contacts admin pages — the browser counterpart of the Contacts JSON
API (apps/contacts/views.py). Mirrors the canonical wired-view pattern in
``apps/organizations/views_htmx.py::members_page``: thin Django HTML views (NOT DRF) that
delegate ALL business logic to ``apps.contacts.services`` (the same layer the API uses) and
gate every action through the single authorization entry point ``apps.common.permissions.can``
(STYLE_GUIDE §e / rbac.md §7).

Pages (TENANT schema; mounted by config/urls.py under ``/<org_slug>/`` — the slug is stripped
by TenantSlugMiddleware before the urlconf matches, ADR-001):
  GET  /<org_slug>/contacts              → contacts_page       (list, search, segments rail)
  GET  /<org_slug>/contacts/<id>         → contact_profile_page (left card + activity/responses)
  POST /<org_slug>/contacts/<id>/delete  → contact_delete      (bulk-bar Delete; GDPR-safe)

ENVIRONMENT RESOLUTION (the routes are ORG-level, but contacts/segments scope to an
Environment — apps/workspaces/models.py). Each request resolves a single ``current environment``
for the routed org with one deterministic rule (shared by the contacts list, contact profile,
and segment-builder pages so they stay consistent):
  1. The readable environments are those whose workspace the caller can at least ``read``
     (``apps.contacts.scoping.environment_access_map`` — bounded queries, no per-row can()).
  2. If ``?environment=<uuid>`` is supplied AND readable → use it; else use the default: the
     first readable env ordered by (workspace name, env type) — production sorts before
     development within a workspace.
  3. Zero readable environments but the caller IS an active org member → render the page at 200
     with the empty-state partial (nothing to show yet). Non-member → 403 (mirrors members_page).

RBAC + 404/403 (STYLE_GUIDE §e):
  - No readable environment a contact lives in → the contact is invisible → 404 (no oracle).
  - A contact the caller can ``view`` but not ``edit`` → the profile renders read-only; the
    delete POST returns 403 (matches builder_views.survey_builder_host's view/edit split).

PII: raw attribute values are shown only to callers with workspace ``write`` on the env
(``scoping.pii_unmasked_for_environment``); read-only callers see the mask. We never compute
RBAC in the template — the view resolves the privilege and passes ready-to-render rows.
"""
from __future__ import annotations

import csv
import io
import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.common.permissions import active_org_membership, can
from apps.contacts import services
from apps.contacts.models import Contact, ContactAttributeKey
from apps.contacts.scoping import (
    environment_access_map,
    pii_unmasked_for_environment,
)
from apps.contacts.serializers import PII_MASK
from apps.segments import filters as segment_filters
from apps.segments.models import Segment

# Cap on contacts scanned for the saved-segment rail counts + the list table (mirrors the
# segments API DEFAULT_EVAL_LIMIT). The in-process segment evaluator is O(contacts) per segment
# (apps/segments/filters.py), so we cap the scan to keep the page bounded on a large env and
# label a capped count as "N+". Exact large-scale counts are a documented future SQL-compiler
# job (FOLLOWUPS / segments.filters module docstring).
RAIL_COUNT_LIMIT = 1000
LIST_PAGE_SIZE = 50

# Well-known attribute keys the list/profile surface as named columns / identity (mirrors the
# contacts dedup key in services.EMAIL_KEY). Anything else is a generic attribute.
EMAIL_KEY = services.EMAIL_KEY
NAME_KEYS = ("name", "full_name", "first_name")  # first match wins for the display name

# CSV import bounds (defensive — keep a bad/huge upload from exhausting memory/time). One bad row
# never kills the batch; each row is reported individually. The cap mirrors LIST_PAGE_SIZE scale
# for a browser-driven admin import; bulk programmatic ingest is the API's job, not this page.
IMPORT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB upload ceiling
IMPORT_MAX_ROWS = 5000  # data rows scanned (excludes the header)


# ---------------------------------------------------------------------------
# Shared environment resolution (the ONE rule used by all three A2 pages).
# ---------------------------------------------------------------------------
def resolve_environment(request):
    """Resolve ``(environment_or_None, access_map)`` for the routed org per the module rule.

    Returns ``(None, {})`` when the caller has no readable environment. Raises ``Http404`` only
    if the routed org cannot be resolved at all (defensive — middleware always sets it for a
    tenant route).
    """
    org = getattr(request, "tenant", None)
    if org is None:
        raise Http404("Organization not resolved.")
    access_map = environment_access_map(request.user)
    if not access_map:
        return None, access_map

    requested = request.GET.get("environment")
    if requested and requested in {str(env_id) for env_id in access_map}:
        # Load the specifically requested (and readable) environment.
        from apps.workspaces.models import Environment

        env = (
            Environment.objects.select_related("workspace")
            .filter(pk=requested)
            .first()
        )
        if env is not None:
            return env, access_map

    # Default: the caller's CURRENT workspace's environment (production preferred), so contacts
    # + segments follow the sidebar workspace switcher. Falls back to the first readable env
    # (e.g. when access is via a team in a workspace that isn't the current one).
    from apps.common.active_workspace import current_workspace
    from apps.workspaces.enums import EnvType
    from apps.workspaces.models import Environment

    cur_ws = current_workspace(request)
    if cur_ws is not None:
        ws_envs = Environment.objects.select_related("workspace").filter(
            workspace=cur_ws, id__in=access_map.keys()
        )
        env = ws_envs.filter(type=EnvType.PRODUCTION).first() or ws_envs.order_by("type", "id").first()
        if env is not None:
            return env, access_map

    env = (
        Environment.objects.select_related("workspace")
        .filter(id__in=access_map.keys())
        .order_by("workspace__name", "type", "id")
        .first()
    )
    return env, access_map


def _is_org_member(request) -> bool:
    return active_org_membership(request.user) is not None


# ---------------------------------------------------------------------------
# Attribute / display helpers (presentation only — no business logic).
# ---------------------------------------------------------------------------
def attr_value_map(contact, *, unmasked: bool) -> dict[str, dict]:
    """``{key: {value, data_type, is_pii}}`` for a contact from prefetched attributes, with PII
    masked unless ``unmasked``. Mirrors the serializer's masking rule (single source = the view's
    ``pii_unmasked_for_environment``)."""
    out: dict[str, dict] = {}
    for attr in contact.attributes.all():  # prefetched
        key = attr.attribute_key
        value = PII_MASK if (key.is_pii and not unmasked) else attr.value
        out[key.key] = {
            "value": value,
            "data_type": key.data_type,
            "is_pii": key.is_pii,
            "label": key.label or key.key,
        }
    return out


def display_name(attr_map: dict[str, dict], contact) -> str:
    for k in NAME_KEYS:
        if k in attr_map and attr_map[k]["value"]:
            return attr_map[k]["value"]
    # Fall back to email, then external id, then a short id.
    if EMAIL_KEY in attr_map and attr_map[EMAIL_KEY]["value"]:
        return attr_map[EMAIL_KEY]["value"]
    return contact.external_id or f"Contact {str(contact.id)[:8]}"


def initials(name: str) -> str:
    parts = [p for p in name.replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _response_stats(contact_ids: list) -> dict:
    """``{contact_id: {"count": n, "last": datetime|None}}`` in ONE aggregate query.

    ``Response.contact_id`` is a plain UUIDField (ADR-008, no FK), so we aggregate by value —
    never a per-contact query (the N+1 the contract forbids). Empty input → empty map.
    """
    if not contact_ids:
        return {}
    from apps.responses.models import Response

    rows = (
        Response.objects.filter(contact_id__in=contact_ids)
        .values("contact_id")
        .annotate(count=Count("id"), last=Max("started_at"))
    )
    return {row["contact_id"]: {"count": row["count"], "last": row["last"]} for row in rows}


# ---------------------------------------------------------------------------
# Pages.
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET"])
def contacts_page(request):
    """``/<org_slug>/contacts`` — the contacts list with search, attribute filters, a saved-
    segments rail, the bulk-action bar, and the add/import affordances. Real Contact rows,
    attribute columns, response counts, and last-seen. Empty env → empty-state partial.

    FILTERS ("Add filter"): the page accepts repeatable ``?filter=<key>:<op>:<value>`` query
    params, evaluated with the SAME (attributeKey, operator, value) primitives segments use —
    so a set of filters is one-click "Save as segment" (the segment builder speaks the identical
    leaf shape). The filters compose into a single ``and`` segment-filter tree and are evaluated
    by the shared ``segment_filters`` evaluator (one source of truth; no re-implemented matching).
    Active filters render as removable chips above the table.
    """
    environment, access_map = resolve_environment(request)

    # No readable environment: member sees an empty page, non-member is forbidden.
    if environment is None:
        if not _is_org_member(request):
            raise PermissionDenied("You are not a member of this organization.")
        return render(
            request,
            "contacts/list.html",
            {
                "environment": None,
                "contacts": [],
                "segments": [],
                "attribute_columns": [],
                "filter_keys": [],
                "filter_operators": _filter_operators(),
                "active_filters": [],
            },
        )

    # Visibility gate (the env must be readable — it is, by construction, but call can() so the
    # single authorization entry point is the source of truth, never a re-implemented check).
    if not can(request.user, "view_contact", environment):
        raise Http404("Not found.")

    can_edit = can(request.user, "edit_contact", environment)
    unmasked = pii_unmasked_for_environment(request.user, environment)
    query = (request.GET.get("q") or "").strip()

    # Parse the repeatable ?filter=key:op:value params into segment-filter leaves + display rows,
    # then narrow via the shared list/export filter path (one source of truth).
    active_filters, filter_leaves = _parse_filter_params(request, environment)
    contacts_qs, filter_leaves = _filtered_contact_qs(environment, query, filter_leaves)

    filtered_total = contacts_qs.count()
    contacts_qs = contacts_qs[:LIST_PAGE_SIZE]
    contact_list = list(contacts_qs)
    stats = _response_stats([c.id for c in contact_list])

    # Decide which attribute keys to surface as table columns: the non-identity, non-PII keys
    # most useful at a glance (company/plan/country/…). Derived from the env's declared keys so
    # the columns are stable across pages (not just whatever the first row happens to have).
    column_keys = list(
        ContactAttributeKey.objects.filter(environment=environment)
        .exclude(key__in=(EMAIL_KEY, *NAME_KEYS))
        .order_by("key")
        .values_list("key", "label")[:3]
    )
    attribute_columns = [{"key": k, "label": label or k} for k, label in column_keys]

    rows = []
    for contact in contact_list:
        attr_map = attr_value_map(contact, unmasked=unmasked)
        name = display_name(attr_map, contact)
        stat = stats.get(contact.id, {})
        last = stat.get("last")
        rows.append(
            {
                "id": contact.id,
                "name": name,
                "initials": initials(name),
                "email": attr_map.get(EMAIL_KEY, {}).get("value", ""),
                "columns": [attr_map.get(col["key"], {}).get("value", "") for col in attribute_columns],
                "response_count": stat.get("count", 0),
                "last_seen": last,
            }
        )

    segments = segment_rail(environment)
    total_count = Contact.objects.filter(environment=environment).count()
    has_filters = bool(query or active_filters)

    # The composed attribute-filter tree as JSON — the source for "Save filter as segment".
    if filter_leaves:
        filter_tree = (
            filter_leaves[0]
            if len(filter_leaves) == 1
            else {"connector": "and", "conditions": filter_leaves}
        )
        filter_json = json.dumps(filter_tree)
    else:
        filter_json = ""

    return render(
        request,
        "contacts/list.html",
        {
            "environment": environment,
            "contacts": rows,
            "attribute_columns": attribute_columns,
            "segments": segments,
            "query": query,
            "can_edit": can_edit,
            "total_count": total_count,
            "filtered_count": filtered_total if has_filters else total_count,
            "has_filters": has_filters,
            # Attribute-key + operator metadata powering the "Add filter" UI. Passed as RAW
            # objects (NOT a |safe JSON string) so the template embeds them via json_script —
            # the same safe JSON-in-HTML pattern the segment builder uses (a label with an
            # apostrophe must not break an attribute-quoted x-data blob).
            "filter_keys": _attribute_key_options(environment),
            "filter_operators": _filter_operators(),
            "active_filters": active_filters,
            # The querystring (without ?new=) used to re-open the same filtered view + as the
            # source filter for "Save as segment".
            "filter_query": _filters_querystring(query, active_filters),
            "filter_json": filter_json,
            "show_new_modal": request.GET.get("new") == "1",
        },
    )


@login_required
@require_http_methods(["GET"])
def contact_profile_page(request, contact_id):
    """``/<org_slug>/contacts/<id>`` — left identity card (attributes + segment pills) and the
    right tabbed area (activity timeline + this contact's responses)."""
    contact = _get_contact_or_404(contact_id)
    if not can(request.user, "view_contact", contact):
        raise Http404("Contact not found.")  # no existence oracle.

    can_edit = can(request.user, "edit_contact", contact)
    environment = contact.environment
    unmasked = pii_unmasked_for_environment(request.user, environment)

    attr_map = attr_value_map(contact, unmasked=unmasked)
    name = display_name(attr_map, contact)

    # Build an ordered attribute list (identity keys first, then the rest by key).
    ordered_attrs = []
    seen = set()
    for k in (EMAIL_KEY, *NAME_KEYS):
        if k in attr_map and k not in seen:
            ordered_attrs.append({"key": k, **attr_map[k]})
            seen.add(k)
    for k in sorted(attr_map):
        if k not in seen:
            ordered_attrs.append({"key": k, **attr_map[k]})
            seen.add(k)

    # Segment-membership pills: which of the env's segments this contact matches. Evaluate the
    # filter tree against THIS contact's prefetched attr_map (cheap — bounded by # segments; no
    # per-segment evaluate_segment scan over the whole environment).
    member_segments = _segments_for_contact(contact)

    # Responses + activity timeline for this contact (one query each; bounded).
    responses = _contact_responses(contact)
    activity = _contact_activity(contact, responses)

    return render(
        request,
        "contacts/profile.html",
        {
            "contact": contact,
            "environment": environment,
            "name": name,
            "initials": initials(name),
            "email": attr_map.get(EMAIL_KEY, {}).get("value", ""),
            "attributes": ordered_attrs,
            "member_segments": member_segments,
            "responses": responses,
            "activity": activity,
            "can_edit": can_edit,
        },
    )


@login_required
@require_http_methods(["POST"])
def contact_delete(request, contact_id):
    """``POST /<org_slug>/contacts/<id>/delete`` — GDPR-safe delete (anonymizes responses) via
    the service. Used by the bulk-action bar's Delete and the profile's delete action. Returns
    403 when the caller lacks ``delete_contact``; redirects to the list on success."""
    contact = _get_contact_or_404(contact_id)
    if not can(request.user, "delete_contact", contact):
        raise PermissionDenied("You do not have permission to delete this contact.")
    services.delete_contact(contact=contact)
    return redirect(_contacts_url(request))


# ---------------------------------------------------------------------------
# Create / edit-attributes / import / export / save-as-segment (the wired actions).
# All delegate to apps.contacts.services (same layer the DRF API uses — NOT a proxy through
# DRF) and gate via can() (rbac.md §7). Mirrors the surveys/segments wired-view pattern.
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["POST"])
def contact_create(request):
    """``POST /<org_slug>/contacts/new`` — create a contact (the "New contact" modal target).

    Builds an attribute list from the posted ``email`` / ``name`` (+ any ``attr_key[]`` /
    ``attr_value[]`` pairs) and calls ``services.upsert_contact`` (dedup-by-email upsert: a
    repeat email merges rather than duplicating — the same behavior the API has). Requires
    ``create_contact`` on the resolved environment (workspace write); a read-only caller → 403.
    On a typed-value error, re-open the modal with the message + redirect to the list otherwise.
    """
    environment = _writable_environment_or_403(request, "create_contact")

    attributes = _attributes_from_post(request)
    if not attributes:
        return redirect(
            _contacts_url(request) + "?new=1&create_error=Add+at+least+an+email+or+name."
        )
    try:
        contact, _created = services.upsert_contact(
            environment=environment,
            external_id=(request.POST.get("external_id") or "").strip() or None,
            attributes=attributes,
            created_by_user_id=request.user.id,
        )
    except services.AttributeValueError as exc:
        msg = "; ".join(e["message"] for e in exc.errors) or "Invalid attribute value(s)."
        from urllib.parse import quote

        return redirect(f"{_contacts_url(request)}?new=1&create_error={quote(msg)}")
    # Land on the new contact's profile (Formbricks-shaped: you just created/identified them).
    return redirect(_contact_url(request, contact.id))


@login_required
@require_http_methods(["POST"])
def contact_add_attribute(request, contact_id):
    """``POST /<org_slug>/contacts/<id>/attributes`` — set/overwrite one attribute on a contact
    (the profile's "Add attribute" target). Uses ``services.update_contact_attributes`` (merge:
    supplied key upserted, others untouched). Unknown keys auto-create as ``string`` (the
    documented low-friction default). Requires ``edit_contact``; read-only caller → 403."""
    contact = _get_contact_or_404(contact_id)
    if not can(request.user, "edit_contact", contact):
        raise PermissionDenied("You do not have permission to edit this contact.")

    key = (request.POST.get("key") or "").strip()
    value = request.POST.get("value")
    if not key:
        return redirect(_contact_url(request, contact.id) + "?attr_error=Attribute+name+is+required.")
    try:
        services.update_contact_attributes(
            contact=contact, attributes=[{"key": key, "value": value if value is not None else ""}]
        )
    except services.AttributeValueError as exc:
        from urllib.parse import quote

        msg = "; ".join(e["message"] for e in exc.errors) or "Invalid attribute value."
        return redirect(f"{_contact_url(request, contact.id)}?attr_error={quote(msg)}")
    return redirect(_contact_url(request, contact.id))


@login_required
@require_http_methods(["GET", "POST"])
def contact_import(request):
    """``/<org_slug>/contacts/import`` — CSV import.

    GET  → the upload modal/page (handled inline in the list via Alpine; this GET renders a
           standalone import page for a direct link / re-display after errors).
    POST → parse the uploaded CSV, upsert each row via ``services.upsert_contact`` (dedup-by-
           email; unknown columns auto-create as string attribute keys), and render a per-row
           RESULTS screen ({row, status: created|updated|error, error?}). One bad row never kills
           the batch (each row is its own try/except). Requires ``create_contact``; else 403.

    Column mapping: the header row's column names map 1:1 to ``ContactAttributeKey.key``. An
    ``email`` column drives dedup (the upsert service); everything else is a normal attribute.
    """
    environment = _writable_environment_or_403(request, "create_contact")

    if request.method == "GET":
        return render(
            request,
            "contacts/import.html",
            {"environment": environment, "org_slug": _slug(request)},
        )

    upload = request.FILES.get("file")
    if upload is None:
        return render(
            request,
            "contacts/import_results.html",
            {"error": "No file was uploaded.", "results": [], "environment": environment},
        )
    if upload.size and upload.size > IMPORT_MAX_BYTES:
        return render(
            request,
            "contacts/import_results.html",
            {
                "error": f"File is too large (max {IMPORT_MAX_BYTES // (1024 * 1024)} MB).",
                "results": [],
                "environment": environment,
            },
        )

    # Decode defensively (utf-8-sig strips a BOM Excel often prepends).
    try:
        raw = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return render(
            request,
            "contacts/import_results.html",
            {
                "error": "Could not read the file as UTF-8 text. Save it as CSV (UTF-8).",
                "results": [],
                "environment": environment,
            },
        )

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        return render(
            request,
            "contacts/import_results.html",
            {"error": "The CSV has no header row.", "results": [], "environment": environment},
        )
    # Normalize header names to attribute keys (strip/lowercase-insensitive trim of surrounding
    # whitespace; the key text itself is preserved as given so labels stay meaningful).
    columns = [(c or "").strip() for c in reader.fieldnames]

    results: list[dict] = []
    created_n = updated_n = error_n = 0
    truncated = False
    for i, row in enumerate(reader, start=1):
        if i > IMPORT_MAX_ROWS:
            truncated = True
            break
        attributes = []
        for col in columns:
            if not col:
                continue
            val = row.get(col)
            if val is None:
                continue
            val = val.strip()
            if val == "":
                continue  # skip empty cells (don't overwrite with blanks)
            attributes.append({"key": col, "value": val})
        if not attributes:
            results.append({"row": i, "status": "error", "error": "Empty row (no values)."})
            error_n += 1
            continue
        try:
            _contact, created = services.upsert_contact(
                environment=environment,
                attributes=attributes,
                created_by_user_id=request.user.id,
            )
        except services.AttributeValueError as exc:
            msg = "; ".join(e["message"] for e in exc.errors) or "Invalid value(s)."
            results.append({"row": i, "status": "error", "error": msg})
            error_n += 1
            continue
        except Exception as exc:  # noqa: BLE001 — a single bad row must not abort the batch.
            results.append({"row": i, "status": "error", "error": str(exc)[:200]})
            error_n += 1
            continue
        if created:
            created_n += 1
            results.append({"row": i, "status": "created"})
        else:
            updated_n += 1
            results.append({"row": i, "status": "updated"})

    return render(
        request,
        "contacts/import_results.html",
        {
            "environment": environment,
            "org_slug": _slug(request),
            "results": results,
            "created_n": created_n,
            "updated_n": updated_n,
            "error_n": error_n,
            "total_n": len(results),
            "truncated": truncated,
            "max_rows": IMPORT_MAX_ROWS,
            "error": None,
        },
    )


def _csv_export_rows(queryset, columns, *, unmasked):
    """Generator yielding CSV lines for the export (streamed; no full-result buffering).

    First line = header (id, external_id, created_at, <each attribute key>). Then one line per
    contact in ``queryset`` (already scoped/filtered by the caller), with PII attribute values
    masked unless ``unmasked`` (same rule as the list page / serializer — single source =
    ``pii_unmasked_for_environment``)."""
    writer = csv.writer(_Echo())
    yield writer.writerow(["id", "external_id", "created_at", *[c["key"] for c in columns]])
    key_is_pii = {c["key"]: c["is_pii"] for c in columns}
    for contact in queryset.iterator(chunk_size=200):
        values = {a.attribute_key.key: a.value for a in contact.attributes.all()}
        cells = [str(contact.id), contact.external_id or "", contact.created_at.isoformat()]
        for col in columns:
            raw = values.get(col["key"], "")
            if key_is_pii.get(col["key"]) and not unmasked and raw:
                raw = PII_MASK
            cells.append(raw)
        yield writer.writerow(cells)


@login_required
@require_http_methods(["GET"])
def contact_export(request):
    """``GET /<org_slug>/contacts/export.csv`` — stream the env's contacts as CSV.

    Requires ``view_contact`` (workspace read). PII columns are masked for a read-only caller
    and shown raw for a write+ caller (``pii_unmasked_for_environment`` — the same rule the list
    and the serializer use). Streamed via ``StreamingHttpResponse`` so a large env doesn't
    buffer the whole file in memory.

    Scope honors the SAME filters the list shows so a filtered Export matches the visible set:
      - ``?ids=<comma-uuids>`` → only that selection (the bulk-bar "Export selected").
      - ``?q=`` + ``?filter=key:op:value`` (repeatable) → the search + attribute filters, narrowed
        via the shared ``_filtered_contact_qs`` (one source of truth with the list page).
      - none → the whole environment.
    """
    environment, _ = resolve_environment(request)
    if environment is None or not can(request.user, "view_contact", environment):
        raise Http404("Not found.")
    unmasked = pii_unmasked_for_environment(request.user, environment)
    columns = [
        {"key": k, "is_pii": is_pii}
        for k, is_pii in ContactAttributeKey.objects.filter(environment=environment)
        .order_by("key")
        .values_list("key", "is_pii")
    ]

    ids_raw = request.GET.get("ids") or ""
    id_filter = [s for s in (x.strip() for x in ids_raw.split(",")) if s]
    if id_filter:
        # A concrete selection (bulk-bar) wins over the search/filter params.
        qs = (
            Contact.objects.filter(environment=environment, id__in=id_filter)
            .prefetch_related("attributes__attribute_key")
            .order_by("created_at", "id")
        )
    else:
        query = (request.GET.get("q") or "").strip()
        _active, filter_leaves = _parse_filter_params(request, environment)
        qs, _ = _filtered_contact_qs(environment, query, filter_leaves)
        qs = qs.order_by("created_at", "id")

    filename = f"contacts-{timezone.now():%Y%m%d}.csv"
    response = StreamingHttpResponse(
        _csv_export_rows(qs, columns, unmasked=unmasked),
        content_type="text/csv",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_http_methods(["POST"])
def contacts_save_as_segment(request):
    """``POST /<org_slug>/contacts/save-as-segment`` — the coherent pivot for "Add to segment".

    DESIGN DECISION (the brief defers this to us): our segments are FILTER-based / DYNAMIC
    (apps/segments) — there is no manual membership table, so a literal "add these N contacts to
    a segment" doesn't fit the model. Instead the bulk-bar's "Add to segment" and the profile's
    "Add to segment" both PIVOT to "save the current selection / current filter AS a segment":

      - With ``contact_ids`` (bulk-bar selection or one profile contact) → resolve those
        contacts' ``email`` values and prefill the segment builder with a leaf
        ``{attributeKey: "email", operator: "isOneOf", value: [...emails]}`` (a contact with no
        email is skipped — it can't be addressed by a dynamic filter). The user reviews + names
        it in the builder, keeping the segment a real, re-evaluable dynamic filter.
      - With ``filter`` (the list's "Save filter as segment") → prefill the builder with the
        in-progress attribute-filter tree directly.

    This view does NOT create the segment itself; it redirects to the segment builder (the
    single create surface) with the filter prefilled via ``?filter=<json>``, so creation still
    flows through ``services.create_segment`` (validation + RBAC + uniqueness in one place).
    Requires ``create_segment`` on the resolved environment; else 403.
    """
    environment, _ = resolve_environment(request)
    if environment is None or not can(request.user, "create_segment", environment):
        raise PermissionDenied("You do not have permission to create segments here.")

    filter_def = None
    # 1. An explicit in-progress filter tree (the list "save filter as segment").
    filter_raw = request.POST.get("filter")
    if filter_raw:
        try:
            parsed = json.loads(filter_raw)
            if isinstance(parsed, dict) and parsed:
                filter_def = parsed
        except (ValueError, TypeError):
            filter_def = None

    # 2. A selection of contact ids → an email isOneOf leaf.
    if filter_def is None:
        ids_raw = request.POST.get("contact_ids") or ""
        ids = [s for s in (x.strip() for x in ids_raw.split(",")) if s]
        emails = _emails_for_contacts(environment, ids)
        if emails:
            filter_def = {
                "attributeKey": EMAIL_KEY,
                "operator": "isOneOf",
                "value": emails,
            }

    if not filter_def:
        # Nothing to build from → bounce back to the list with a gentle note.
        return redirect(
            _contacts_url(request)
            + "?segment_error=Select+contacts+with+an+email+or+apply+a+filter+first."
        )

    from urllib.parse import quote

    return redirect(f"{_segment_new_url(request)}?filter={quote(json.dumps(filter_def))}")


# ---------------------------------------------------------------------------
# Action helpers (write target resolution, payload parsing, filter parsing).
# ---------------------------------------------------------------------------
class _Echo:
    """A write-only file-like object whose ``write`` returns the value (for ``csv.writer`` +
    ``StreamingHttpResponse`` — the canonical Django CSV-streaming idiom)."""

    def write(self, value):
        return value


def _writable_environment_or_403(request, action: str):
    """Resolve the env the contacts pages operate in and assert ``action`` on it; else 403.

    The contacts pages resolve a single deterministic environment (``resolve_environment``);
    creates/imports must hold the workspace ``write``-level ``action`` there. A caller who can
    only read the env (or no env) → ``PermissionDenied`` (matches the segment-builder 403 path).
    """
    environment, _ = resolve_environment(request)
    if environment is None or not can(request.user, action, environment):
        raise PermissionDenied("You do not have permission to do that here.")
    return environment


def _attributes_from_post(request) -> list[dict]:
    """Build a contact-write ``attributes`` list from the New-contact modal fields.

    Accepts the well-known ``email`` / ``name`` inputs plus any extra ``attr_key``/``attr_value``
    pairs (parallel arrays). Blank keys/values are dropped; duplicate keys keep the last value.
    """
    attrs: dict[str, str] = {}
    email = (request.POST.get("email") or "").strip()
    if email:
        attrs[EMAIL_KEY] = email
    name = (request.POST.get("name") or "").strip()
    if name:
        attrs["name"] = name
    extra_keys = request.POST.getlist("attr_key")
    extra_vals = request.POST.getlist("attr_value")
    for k, v in zip(extra_keys, extra_vals, strict=False):
        k = (k or "").strip()
        v = (v or "").strip()
        if k and v:
            attrs[k] = v
    return [{"key": k, "value": v} for k, v in attrs.items()]


def _emails_for_contacts(environment, contact_ids) -> list[str]:
    """The distinct ``email`` attribute values of the given contacts (within ``environment``),
    for the "save selection as segment" pivot. Contacts without an email are skipped (a dynamic
    filter can only address them by attribute). Order-stable + deduplicated (case-preserving)."""
    from apps.contacts.models import ContactAttribute

    if not contact_ids:
        return []
    rows = (
        ContactAttribute.objects.filter(
            contact__environment=environment,
            contact_id__in=contact_ids,
            attribute_key__key=EMAIL_KEY,
        )
        .values_list("value", flat=True)
    )
    seen: set[str] = set()
    out: list[str] = []
    for value in rows:
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out


def _attribute_key_options(environment) -> list[dict]:
    """``[{key, label, data_type}]`` for the env's attribute keys — powers the "Add filter"
    dropdown + the New-contact custom-attribute picker. Reuses the model directly (no extra
    abstraction); ordered by key for a stable UI."""
    return [
        {"key": k, "label": label or k, "data_type": dtype}
        for k, label, dtype in ContactAttributeKey.objects.filter(environment=environment)
        .order_by("key")
        .values_list("key", "label", "data_type")
    ]


def _filter_operators() -> dict:
    """``{data_type: [{op, label, needs_value, is_array}]}`` from the FROZEN segments operator
    table — the same source the segment builder uses (never hardcoded). The list page's "Add
    filter" dropdown reads this (via json_script) so a contact filter and a segment leaf are
    guaranteed compatible. Returned as a raw dict for safe json_script embedding."""
    from apps.segments.views_htmx import _operators_by_type

    # _operators_by_type already returns {data_type: [{op, label, needs_value, is_array}]}.
    return _operators_by_type()


def _parse_filter_params(request, environment):
    """Parse repeatable ``?filter=key:op:value`` params into (display rows, segment-filter leaves).

    Each param is ``<attributeKey>:<operator>[:<value>]``. The leaf shape matches segments
    exactly (``{attributeKey, operator, value?}``) so the same evaluator runs and a one-click
    "Save as segment" produces an identical, valid filter. Unknown keys / malformed params are
    skipped silently (the list never errors on a bad URL). Presence operators (isSet/...) carry
    no value. ``value`` is split on ``|`` for array operators (isOneOf).
    """
    key_types = dict(
        ContactAttributeKey.objects.filter(environment=environment).values_list(
            "key", "data_type"
        )
    )
    no_value = segment_filters._NO_VALUE_OPS
    array_ops = segment_filters._ARRAY_VALUE_OPS
    valid_by_type = segment_filters.VALID_OPERATORS_BY_TYPE

    display: list[dict] = []
    leaves: list[dict] = []
    for raw in request.GET.getlist("filter"):
        parts = (raw or "").split(":", 2)
        if len(parts) < 2:
            continue
        key, op = parts[0].strip(), parts[1].strip()
        value = parts[2] if len(parts) == 3 else ""
        dtype = key_types.get(key)
        if dtype is None:
            continue  # unknown attribute key
        if op not in valid_by_type.get(dtype, frozenset()):
            continue  # operator not valid for this key's type
        leaf: dict = {"attributeKey": key, "operator": op}
        if op not in no_value:
            if op in array_ops:
                vals = [v.strip() for v in value.split("|") if v.strip()]
                if not vals:
                    continue
                leaf["value"] = vals
            else:
                if value == "":
                    continue
                leaf["value"] = value
        leaves.append(leaf)
        display.append(
            {
                "raw": raw,
                "key": key,
                "label": key,
                "operator": op,
                "value": value,
            }
        )
    return display, leaves


def _filtered_contact_qs(environment, query: str, filter_leaves: list[dict]):
    """Apply the list's search (``query``) + attribute filters (``filter_leaves``) to the env's
    contacts and return the (queryset, surviving_leaves) — the SINGLE narrowing path shared by the
    list page and the export so a filtered Export matches the visible list.

    The attribute filters are evaluated with the SAME segments evaluator (one source of truth); a
    malformed/out-of-bounds tree is ignored (returned ``surviving_leaves`` reflects what applied).
    """
    qs = (
        Contact.objects.filter(environment=environment)
        .prefetch_related("attributes__attribute_key")
        .order_by("-created_at", "id")
    )
    if query:
        qs = qs.filter(attributes__value__icontains=query).distinct()
    surviving = filter_leaves
    if filter_leaves:
        tree = (
            filter_leaves[0]
            if len(filter_leaves) == 1
            else {"connector": "and", "conditions": filter_leaves}
        )
        try:
            matched = segment_filters.evaluate_segment(
                tree, environment, limit=RAIL_COUNT_LIMIT
            )
            qs = qs.filter(id__in=[c.id for c in matched])
        except segment_filters.FilterValidationError:
            surviving = []
    return qs, surviving


def _filters_querystring(query: str, active_filters: list[dict]) -> str:
    """Rebuild the list's querystring (``q`` + each ``filter=``) from the active filter state —
    used to keep the filtered view on pagination/refresh and as the "Save as segment" source."""
    from urllib.parse import urlencode

    pairs: list[tuple[str, str]] = []
    if query:
        pairs.append(("q", query))
    for f in active_filters:
        pairs.append(("filter", f["raw"]))
    return urlencode(pairs)


# ---------------------------------------------------------------------------
# Internal data helpers.
# ---------------------------------------------------------------------------
def _get_contact_or_404(contact_id) -> Contact:
    try:
        return (
            Contact.objects.select_related("environment__workspace")
            .prefetch_related("attributes__attribute_key")
            .get(pk=contact_id)
        )
    except (Contact.DoesNotExist, ValueError):
        raise Http404("Contact not found.") from None


def segment_rail(environment) -> list[dict]:
    """The saved-segments rail: every segment in the env + a (capped) matching-contact count.

    The evaluator is O(contacts) per segment; we cap the scan at RAIL_COUNT_LIMIT and flag a
    capped count so the rail is bounded on a large environment (documented trade-off)."""
    segments = list(Segment.objects.filter(environment=environment).order_by("title", "id"))
    out = []
    for seg in segments:
        matched = segment_filters.evaluate_segment(
            seg.filter, environment, limit=RAIL_COUNT_LIMIT
        )
        count = len(matched)
        out.append(
            {
                "id": seg.id,
                "title": seg.title,
                "count": count,
                "count_capped": count >= RAIL_COUNT_LIMIT,
            }
        )
    return out


def _segments_for_contact(contact) -> list[dict]:
    """Which of the env's segments THIS contact matches (membership pills). Evaluates each
    segment's filter against the contact's prefetched attributes — bounded by # segments."""
    environment = contact.environment
    key_types = dict(
        ContactAttributeKey.objects.filter(environment=environment).values_list(
            "key", "data_type"
        )
    )
    # {key: (data_type, stored_value_or_None)} for this contact (reuse the evaluator's shape).
    attr_map = {key: (dtype, None) for key, dtype in key_types.items()}
    for attr in contact.attributes.all():  # prefetched
        attr_map[attr.attribute_key.key] = (attr.attribute_key.data_type, attr.value)

    segments = Segment.objects.filter(environment=environment).order_by("title", "id")
    out = []
    for seg in segments:
        try:
            if segment_filters.matches(seg.filter, attr_map):
                out.append({"id": seg.id, "title": seg.title})
        except segment_filters.FilterValidationError:
            # A stored filter that exceeds the depth bound never crashes the profile page.
            continue
    return out


def _contact_responses(contact) -> list[dict]:
    """This contact's responses (newest first), with the survey name for display. One query for
    the responses, one for the survey names (bounded)."""
    from apps.responses.models import Response
    from apps.surveys.models import Survey

    responses = list(
        Response.objects.filter(contact_id=contact.id).order_by("-started_at")[:50]
    )
    survey_ids = {r.survey_id for r in responses}
    names = dict(Survey.objects.filter(id__in=survey_ids).values_list("id", "name"))
    out = []
    for r in responses:
        out.append(
            {
                "id": r.id,
                "survey_name": names.get(r.survey_id, "Survey"),
                "finished": r.finished,
                "started_at": r.started_at,
                "answer_count": len(r.data or {}),
            }
        )
    return out


def _contact_activity(contact, responses) -> list[dict]:
    """A simple activity timeline derived from real signals: the contact's creation event +
    one entry per response (submitted / started). Newest first."""
    items = []
    for r in responses:
        if r["finished"]:
            items.append(
                {
                    "icon": "task_alt",
                    "text": f"Submitted {r['survey_name']}",
                    "at": r["started_at"],
                }
            )
        else:
            items.append(
                {
                    "icon": "draft",
                    "text": f"Started {r['survey_name']}",
                    "at": r["started_at"],
                }
            )
    items.append(
        {"icon": "person_add", "text": "Contact created", "at": contact.created_at}
    )
    items.sort(key=lambda i: i["at"], reverse=True)
    return items


# ---------------------------------------------------------------------------
# URL helpers (keep the org-prefixed paths in one place).
# ---------------------------------------------------------------------------
def _slug(request):
    return getattr(getattr(request, "tenant", None), "slug", None)


def _contacts_url(request) -> str:
    slug = _slug(request)
    return f"/{slug}/contacts" if slug else "/"


def _contact_url(request, contact_id) -> str:
    slug = _slug(request)
    return f"/{slug}/contacts/{contact_id}" if slug else "/"


def _segments_url(request) -> str:
    slug = _slug(request)
    return f"/{slug}/segments" if slug else "/"


def _segment_new_url(request) -> str:
    slug = _slug(request)
    return f"/{slug}/segments/new" if slug else "/"
