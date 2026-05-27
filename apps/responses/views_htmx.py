"""
Server-rendered (HTMX) response admin pages — the browser counterpart of the Response READ
JSON API (Wave 3b admin surface). Thin Django HTML views (NOT DRF) following the canonical
``apps.organizations.views_htmx.members_page`` pattern:

  @login_required → resolve ``request.tenant`` → RBAC via ``can()`` → models → render.

Authorization delegates to the single entry point ``apps.common.permissions.can()`` (rbac.md
§7). The READ pages require ``view_response`` (workspace ``read``); applying/removing a tag
requires ``tag_response`` (workspace ``write``); exporting requires ``export_responses``
(workspace ``read``). A survey/response the caller cannot SEE → ``Http404`` (no existence
oracle), mirroring ``apps/responses/views.py`` + ``apps/surveys/builder_views.py``.

TAG MANAGEMENT RBAC (the documented decision): ``Tag`` is tenant-GLOBAL (no workspace/env FK —
models.md §2.9), so there is no Workspace to pass to a workspace-scoped ``can()`` for the
rename/delete/merge management ops. Rather than invent a new entry in the FROZEN rbac matrix
(apps/common is out of this surface's territory), the management routes gate on ORG role
owner/admin via the public ``active_org_membership`` helper already exported from
``apps.common.permissions`` — managing the org-wide tag vocabulary is an admin concern, the
same level the rbac matrix already uses for other tenant-global admin surfaces (audit log,
team lifecycle). Applying/removing a tag to a SPECIFIC response stays on the per-response
``tag_response`` workspace check (it scopes to that response's workspace). Both are documented
on the relevant views below.

Pages (tenant schema, under ``/<org_slug>/`` — slug stripped by TenantSlugMiddleware):
  /<org_slug>/surveys/<id>/responses              survey_responses  (table + filters + tags + export)
  /<org_slug>/surveys/<id>/responses/<rid>        response_detail   (single response Q&A + tags)
  /<org_slug>/surveys/<id>/responses.csv|.json    response_export   (filtered, RBAC + PII masked)
  /<org_slug>/surveys/<id>/responses/<rid>/tags          response_tag_apply  (POST add tag)
  /<org_slug>/surveys/<id>/responses/<rid>/tags/<tid>/remove  response_tag_remove (POST remove)
  /<org_slug>/tags                                tags_manage       (rename/delete/merge — admin)

The pure-Python answer helpers (``_i18n`` / ``_is_answered``) are reused from
``apps.surveys.views_htmx`` so the table/detail rendering matches the results page's logic.
"""
from __future__ import annotations

import csv

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.common.permissions import active_org_membership, can
from apps.contacts.scoping import pii_unmasked_for_environment
from apps.organizations.enums import OrgRole
from apps.surveys.models import Survey
from apps.surveys.views_htmx import _i18n, _is_answered

from . import tag_services
from .models import Response

# Rows per page for the responses table (page-number pagination — keeps the table bounded).
_PAGE_SIZE = 25

# IP key inside Response.meta is PII (models.py §Response, rbac.md §11) — masked on export for
# a read-only caller. A resolved contact's email is also PII (mirrors _contact_label).
_PII_MASK = "***"


def _get_survey_or_404(survey_id) -> Survey:
    """Load a survey with its workspace chain (for ``can()``) or 404 (mirrors responses/views)."""
    try:
        return Survey.objects.select_related(
            "environment", "environment__workspace"
        ).get(pk=survey_id)
    except (Survey.DoesNotExist, ValueError) as exc:
        raise Http404("Survey not found.") from exc


def _nps_band(value: int) -> str:
    """NPS band for a 0..10 score (promoter 9-10 / passive 7-8 / detractor 0-6)."""
    if value >= 9:
        return "promoter"
    if value >= 7:
        return "passive"
    return "detractor"


def _render_cell(data: dict, col: dict) -> dict:
    """Render one response-table cell for a question column.

    Returns ``{kind, ...}`` so the template styles NPS chips / rating stars / text:
      - nps    → {kind: "nps", value, band}  (band ∈ promoter|passive|detractor)
      - rating → {kind: "rating", value}
      - else   → {kind: "text", text}        ("—" + skipped flag when unanswered)
    """
    qid = col["id"]
    qtype = col["type"]
    val = data.get(qid)
    if not _is_answered(val):
        return {"kind": "text", "text": "—", "skipped": True}

    if qtype == "nps" and isinstance(val, int) and not isinstance(val, bool):
        return {"kind": "nps", "value": val, "band": _nps_band(val)}
    if qtype == "rating" and isinstance(val, int) and not isinstance(val, bool):
        rng = col.get("range", 5)
        # Pre-build the filled/empty star flags so the template iterates a list (works for any
        # range; a template range(N) / make_list-of-int is unreliable for 2-digit ranges).
        stars = [step <= val for step in range(1, rng + 1)]
        return {"kind": "rating", "value": val, "range": rng, "stars": stars}
    if isinstance(val, list):
        return {"kind": "text", "text": ", ".join(str(v) for v in val)}
    if isinstance(val, dict):
        cid = val.get("choiceId")
        if cid is not None:
            return {"kind": "text", "text": str(cid)}
        parts = [str(v) for v in val.values() if isinstance(v, str | int | float) and str(v)]
        return {"kind": "text", "text": ", ".join(parts) or "—"}
    return {"kind": "text", "text": str(val)}


def _questions_by_id(survey: Survey) -> dict:
    """``{qid: headline}`` for the survey's questions — drives the answer-filter dropdown + chips
    (the human label for a ``answer=<qid>:op:value`` filter). Resolved via the shared i18n rule.
    """
    return {
        q["id"]: _i18n(q.get("headline")) or q["id"]
        for q in (survey.questions or [])
        if isinstance(q, dict) and "id" in q
    }


def _active_filters(request, survey):
    """Resolve the active responses filters from the querystring → (filter_state, queryset).

    Reads (all optional, AND-combined): ``?tag=<uuid>`` (repeatable), ``?status=completed|partial``,
    ``?from=YYYY-MM-DD``, ``?to=YYYY-MM-DD``, ``?answer=<qid>:op:value`` (repeatable). Builds the
    filtered queryset via the SHARED ``tag_services.filtered_responses`` (one source of truth with
    the export) and returns a ``filter_state`` dict the table/export use to render active chips +
    rebuild the querystring. Invalid tokens are dropped silently (the table never 500s on a bad URL).
    """
    questions_by_id = _questions_by_id(survey)

    # Tags: keep only ids that resolve to a real tag (drop garbage), preserving order.
    raw_tag_ids = request.GET.getlist("tag")
    tag_objs = []
    seen = set()
    for tid in raw_tag_ids:
        if tid in seen:
            continue
        tag = tag_services.get_tag_or_none(tid)
        if tag is not None:
            tag_objs.append(tag)
            seen.add(tid)
    tag_ids = [t.id for t in tag_objs]

    status = request.GET.get("status", "")
    if status not in ("completed", "partial"):
        status = ""
    date_from = (request.GET.get("from") or "").strip()
    date_to = (request.GET.get("to") or "").strip()

    answer_display, answer_specs = tag_services.parse_answer_filters(
        request.GET.getlist("answer"), questions_by_id
    )

    qs = tag_services.filtered_responses(
        survey,
        tag_ids=tag_ids,
        status=status,
        date_from=date_from,
        date_to=date_to,
        answer_specs=answer_specs,
    )

    has_filters = bool(tag_ids or status or date_from or date_to or answer_specs)
    state = {
        "tags": tag_objs,
        "tag_ids": [str(t.id) for t in tag_objs],
        "status": status,
        "from": date_from,
        "to": date_to,
        "answers": answer_display,
        "has_filters": has_filters,
    }
    return state, qs


def _filter_querystring(state) -> str:
    """Rebuild the active-filter querystring (``tag=&status=&from=&to=&answer=``) — used to keep
    the filtered view on pagination + as the export link's query."""
    from urllib.parse import urlencode

    pairs: list[tuple[str, str]] = []
    for tid in state["tag_ids"]:
        pairs.append(("tag", tid))
    if state["status"]:
        pairs.append(("status", state["status"]))
    if state["from"]:
        pairs.append(("from", state["from"]))
    if state["to"]:
        pairs.append(("to", state["to"]))
    for a in state["answers"]:
        pairs.append(("answer", a["raw"]))
    return urlencode(pairs)


@login_required
@require_http_methods(["GET"])
def survey_responses(request, survey_id):
    """``/<org_slug>/surveys/<id>/responses`` — paginated real Response rows + filters + tags.

    Requires ``view_response`` (workspace ``read``); invisible survey → 404. Columns derive from
    the survey's questions (capped at 6 for readability); NPS → chip, rating → stars, else text.
    Filters narrow the set (tag/status/date/answer — see ``_active_filters``); tag chips render
    per row; the Export button links to the CSV honoring the active filters. Tagging affordances
    render only for a caller with ``tag_response`` (workspace write).
    """
    survey = _get_survey_or_404(survey_id)
    if not can(request.user, "view_response", survey):
        raise Http404("Survey not found.")

    questions = [q for q in (survey.questions or []) if isinstance(q, dict) and "id" in q]
    columns = [
        {
            "id": q["id"],
            "type": q.get("type", ""),
            "headline": _i18n(q.get("headline")) or q["id"],
            "range": int((q.get("config") or {}).get("range", 5)),
        }
        for q in questions[:6]
    ]

    filter_state, qs = _active_filters(request, survey)

    try:
        page_num = max(1, int(request.GET.get("page", "1")))
    except (TypeError, ValueError):
        page_num = 1
    total = qs.count()
    start = (page_num - 1) * _PAGE_SIZE
    page_rows = list(qs[start : start + _PAGE_SIZE])

    # Tag hydration for the visible page (ONE query, no per-row N+1).
    tags_by_resp = tag_services.tags_for_responses([r.id for r in page_rows])

    rows = []
    for resp in page_rows:
        rows.append(
            {
                "id": resp.id,
                "finished": resp.finished,
                "created_at": resp.created_at,
                "contact_id": resp.contact_id,
                "cells": [_render_cell(resp.data or {}, col) for col in columns],
                "tags": tags_by_resp.get(resp.id, []),
            }
        )

    can_tag = can(request.user, "tag_response", survey)
    can_export = can(request.user, "export_responses", survey)
    filter_qs = _filter_querystring(filter_state)
    total_unfiltered = Response.objects.filter(survey=survey).count()

    total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE if total else 1
    context = {
        "org_slug": request.tenant.slug,
        "survey": survey,
        "columns": columns,
        "rows": rows,
        "total": total,
        "total_unfiltered": total_unfiltered,
        "page": page_num,
        "total_pages": total_pages,
        "has_prev": page_num > 1,
        "has_next": page_num < total_pages,
        "prev_page": page_num - 1,
        "next_page": page_num + 1,
        "row_start": start + 1 if total else 0,
        "row_end": min(start + _PAGE_SIZE, total),
        # Tags + filters.
        "can_tag": can_tag,
        "can_export": can_export,
        # Tag objects for the row "add tag" picker + chips.
        "all_tags": [r["tag"] for r in tag_services.all_tags_with_counts()],
        "filter_state": filter_state,
        "filter_qs": filter_qs,
        # JSON-serializable shapes for the filter modal's json_script blocks (safe JSON-in-HTML;
        # a tag/question label with an apostrophe can't break the markup).
        "all_tags_json": [
            {"id": str(r["tag"].id), "name": r["tag"].name}
            for r in tag_services.all_tags_with_counts()
        ],
        "questions_json": [
            {"id": q["id"], "headline": _i18n(q.get("headline")) or q["id"]} for q in questions
        ],
        "answer_operators": list(tag_services.ANSWER_OPERATORS.keys()),
    }
    return render(request, "responses/table.html", context)


@login_required
@require_http_methods(["GET"])
def response_detail(request, survey_id, response_id):
    """``/<org_slug>/surveys/<id>/responses/<rid>`` — single response Q&A.

    Requires ``view_response`` (workspace ``read``); invisible survey/response → 404. Renders
    every question with its rendered answer (NPS scale, rating stars, choice labels, text),
    mirroring the response-detail mockup (rendered as a page; the design's drawer holds the
    identical data).
    """
    survey = _get_survey_or_404(survey_id)
    if not can(request.user, "view_response", survey):
        raise Http404("Survey not found.")

    try:
        resp = Response.objects.get(pk=response_id, survey=survey)
    except (Response.DoesNotExist, ValueError) as exc:
        raise Http404("Response not found.") from exc

    context = {
        "org_slug": request.tenant.slug,
        "survey": survey,
        "response": resp,
        "answers": _response_answers(survey, resp),
        # Tags on this response + the full tenant vocabulary for the add picker. The add/remove
        # affordances render only for a ``tag_response`` (workspace write) caller.
        "tags": tag_services.tags_for_response(resp),
        "all_tags": [r["tag"] for r in tag_services.all_tags_with_counts()],
        "can_tag": can(request.user, "tag_response", survey),
    }
    return render(request, "responses/detail.html", context)


# ---------------------------------------------------------------------------
# Tag apply / remove (per-response — gated by ``tag_response`` = workspace write).
# Both delegate ALL business logic to ``tag_services`` (request-free) and redirect back to the
# referring view (the detail page by default, the table when ``?back=table`` is present), so the
# action works equally from the single-response view and the table row.
# ---------------------------------------------------------------------------
def _get_response_or_404(survey, response_id) -> Response:
    try:
        return Response.objects.get(pk=response_id, survey=survey)
    except (Response.DoesNotExist, ValueError) as exc:
        raise Http404("Response not found.") from exc


def _back_url(request, survey, response_id) -> str:
    """Where to land after a tag mutation: the table (with the active filters preserved) when the
    form posted ``back=table``; otherwise the single-response detail page."""
    slug = request.tenant.slug
    if (request.POST.get("back") or request.GET.get("back")) == "table":
        qs = request.POST.get("filter_qs") or ""
        suffix = f"?{qs}" if qs else ""
        return f"/{slug}/surveys/{survey.id}/responses{suffix}"
    return f"/{slug}/surveys/{survey.id}/responses/{response_id}"


@login_required
@require_http_methods(["POST"])
def response_tag_apply(request, survey_id, response_id):
    """``POST /<org_slug>/surveys/<id>/responses/<rid>/tags`` — add a tag to a response.

    Create-on-the-fly: a posted ``tag_name`` creates (or reuses, case-insensitively) a tenant
    tag and links it; a posted ``tag_id`` links an existing tag. Requires ``tag_response``
    (workspace write); a read-only caller → 403. Idempotent (re-applying is a no-op).
    """
    survey = _get_survey_or_404(survey_id)
    if not can(request.user, "view_response", survey):
        raise Http404("Survey not found.")
    if not can(request.user, "tag_response", survey):
        raise PermissionDenied("You do not have permission to tag responses.")

    resp = _get_response_or_404(survey, response_id)

    tag_id = (request.POST.get("tag_id") or "").strip()
    tag_name = (request.POST.get("tag_name") or "").strip()
    if tag_id:
        tag = tag_services.get_tag_or_none(tag_id)
        if tag is not None:
            tag_services.apply_tag(resp, tag)
    elif tag_name:
        tag = tag_services.tag_create_or_get(tag_name, created_by_user_id=request.user.id)
        tag_services.apply_tag(resp, tag)
    return redirect(_back_url(request, survey, response_id))


@login_required
@require_http_methods(["POST"])
def response_tag_remove(request, survey_id, response_id, tag_id):
    """``POST /<org_slug>/surveys/<id>/responses/<rid>/tags/<tid>/remove`` — unlink a tag from a
    response. Requires ``tag_response`` (workspace write); read-only caller → 403. Idempotent."""
    survey = _get_survey_or_404(survey_id)
    if not can(request.user, "view_response", survey):
        raise Http404("Survey not found.")
    if not can(request.user, "tag_response", survey):
        raise PermissionDenied("You do not have permission to tag responses.")

    resp = _get_response_or_404(survey, response_id)
    tag = tag_services.get_tag_or_none(tag_id)
    if tag is not None:
        tag_services.remove_tag(resp, tag)
    return redirect(_back_url(request, survey, response_id))


def _response_answers(survey: Survey, resp: Response) -> list[dict]:
    """Build the per-question rendered answers for one response (response-detail page)."""
    data = resp.data or {}
    out: list[dict] = []
    for idx, q in enumerate(survey.questions or [], start=1):
        if not isinstance(q, dict) or "id" not in q:
            continue
        qid = q["id"]
        qtype = q.get("type", "")
        val = data.get(qid)
        config = q.get("config") or {}
        entry = {
            "index": idx,
            "headline": _i18n(q.get("headline")) or qid,
            "type": qtype,
            "answered": _is_answered(val),
        }
        if not _is_answered(val):
            entry["kind"] = "skipped"
        elif qtype == "nps":
            entry["kind"] = "nps"
            entry["value"] = val
            entry["scale"] = list(range(0, 11))
        elif qtype == "rating":
            entry["kind"] = "rating"
            rating_val = val if isinstance(val, int) and not isinstance(val, bool) else 0
            rng = int(config.get("range", 5))
            entry["value"] = rating_val
            entry["range"] = rng
            entry["stars"] = [step <= rating_val for step in range(1, rng + 1)]
        elif qtype in ("singleSelect", "multiSelect", "pictureChoice"):
            labels = {
                c.get("id"): _i18n(c.get("label"))
                for c in (config.get("choices") or [])
                if isinstance(c, dict)
            }
            selected = (
                val
                if isinstance(val, list)
                else ([val.get("choiceId")] if isinstance(val, dict) else [val])
            )
            entry["kind"] = "choice"
            entry["all_choices"] = [
                {"label": labels.get(c.get("id"), ""), "selected": c.get("id") in selected}
                for c in (config.get("choices") or [])
                if isinstance(c, dict)
            ]
            # Fallback: a selected id not present in the declared choices still shows.
            declared = {c.get("id") for c in (config.get("choices") or []) if isinstance(c, dict)}
            for cid in selected:
                if cid is not None and cid not in declared:
                    entry["all_choices"].append({"label": str(cid), "selected": True})
        else:
            if isinstance(val, dict):
                text = ", ".join(
                    str(v) for v in val.values() if isinstance(v, str | int | float) and str(v)
                )
            elif isinstance(val, list):
                text = ", ".join(str(v) for v in val)
            else:
                text = str(val)
            entry["kind"] = "text"
            entry["text"] = text
        out.append(entry)
    return out


# ===========================================================================
# Tag MANAGEMENT (rename / delete / merge) — tenant-GLOBAL, gated on ORG owner/admin.
#
# Lives under ``/<org_slug>/tags`` (the responses area owns it — tags label responses). Because
# Tag has no workspace FK, the management ops use the org-role gate (see the module docstring's
# "TAG MANAGEMENT RBAC" note) rather than a workspace-scoped ``can()``.
# ===========================================================================
def _require_tag_admin(request):
    """Assert the caller is an active org owner/admin (the tag-management privilege); else raise.

    A non-member → 404 (no existence oracle for the tags surface); a plain member → 403 (they
    are in the org but lack the admin privilege to reshape the shared tag vocabulary).
    """
    membership = active_org_membership(request.user)
    if membership is None:
        raise Http404("Not found.")
    if membership.role not in (OrgRole.OWNER, OrgRole.ADMIN):
        raise PermissionDenied("Only organization owners/admins can manage tags.")
    return membership


def _tags_url(request) -> str:
    return f"/{request.tenant.slug}/tags"


@login_required
@require_http_methods(["GET"])
def tags_manage(request):
    """``GET /<org_slug>/tags`` — the tag-management page: every tag, its response count, and
    rename / delete / merge affordances. Owner/admin only (tenant-global vocabulary)."""
    _require_tag_admin(request)
    tags = tag_services.all_tags_with_counts()
    return render(
        request,
        "responses/tags_manage.html",
        {
            "org_slug": request.tenant.slug,
            "tags": tags,
            "error": request.GET.get("error", ""),
            "notice": request.GET.get("notice", ""),
        },
    )


@login_required
@require_http_methods(["POST"])
def tag_rename(request, tag_id):
    """``POST /<org_slug>/tags/<id>/rename`` — rename a tag (owner/admin). A blank/duplicate name
    bounces back with an error note."""
    _require_tag_admin(request)
    tag = tag_services.get_tag_or_none(tag_id)
    if tag is None:
        raise Http404("Tag not found.")
    from urllib.parse import quote

    try:
        tag_services.rename_tag(tag, request.POST.get("name", ""))
    except ValueError as exc:
        return redirect(f"{_tags_url(request)}?error={quote(str(exc))}")
    return redirect(_tags_url(request))


@login_required
@require_http_methods(["POST"])
def tag_delete(request, tag_id):
    """``POST /<org_slug>/tags/<id>/delete`` — delete a tag (owner/admin); ResponseTag links
    cascade, responses are untouched."""
    _require_tag_admin(request)
    tag = tag_services.get_tag_or_none(tag_id)
    if tag is None:
        raise Http404("Tag not found.")
    tag_services.delete_tag(tag)
    return redirect(_tags_url(request))


@login_required
@require_http_methods(["POST"])
def tag_merge(request, tag_id):
    """``POST /<org_slug>/tags/<id>/merge`` — merge this (source) tag INTO the posted ``target``
    tag (owner/admin). Re-points the source's response links to the target then deletes the
    source. A missing/identical target bounces back with an error note."""
    _require_tag_admin(request)
    source = tag_services.get_tag_or_none(tag_id)
    if source is None:
        raise Http404("Tag not found.")
    from urllib.parse import quote

    target = tag_services.get_tag_or_none((request.POST.get("target") or "").strip())
    if target is None:
        return redirect(f"{_tags_url(request)}?error={quote('Choose a tag to merge into.')}")
    try:
        gained = tag_services.merge_tags(source=source, target=target)
    except ValueError as exc:
        return redirect(f"{_tags_url(request)}?error={quote(str(exc))}")
    return redirect(
        f"{_tags_url(request)}?notice={quote(f'Merged into “{target.name}” ({gained} updated).')}"
    )


# ===========================================================================
# EXPORT — stream the filtered responses as CSV (and JSON), RBAC + PII masked.
# ===========================================================================
class _Echo:
    """Write-only file-like whose ``write`` returns the value — the canonical Django CSV-stream
    idiom (``csv.writer`` + ``StreamingHttpResponse``). Mirrors the contacts exporter."""

    def write(self, value):
        return value


def _respondent_label(resp: Response, *, contacts_by_id: dict, unmasked: bool) -> str:
    """Identity column for a response: a contact's email (PII — masked for read-only callers,
    mirroring ``surveys.views_htmx._contact_label``) or "Anonymous". Falls back to a short
    contact id if the contact has no email."""
    if resp.contact_id is None:
        return "Anonymous"
    contact = contacts_by_id.get(resp.contact_id)
    if contact is None:
        return f"Contact {str(resp.contact_id)[:8]}"
    from apps.contacts.views_htmx import EMAIL_KEY, attr_value_map, display_name

    attr_map = attr_value_map(contact, unmasked=unmasked)
    email = attr_map.get(EMAIL_KEY, {}).get("value", "")
    return email or display_name(attr_map, contact)


def _meta_ip(meta: dict, *, unmasked: bool) -> str:
    """The response's source IP (meta.ip) — PII (models.py §Response). Masked for read-only
    callers (same write+ gate as contact email)."""
    ip = (meta or {}).get("ip", "")
    if ip and not unmasked:
        return _PII_MASK
    return ip or ""


def _export_record(resp, columns, *, contacts_by_id, tags_by_resp, unmasked):
    """Build the flat ordered values for one response export record (shared by CSV + JSON).

    Columns: id, respondent (PII-masked), status, language, ip (PII-masked), submitted_at,
    tags, then one column per question (flattened answer text). Returns a list of (key, value)
    so the CSV header + the JSON object stay in lockstep.
    """
    answers = resp.data or {}
    record = [
        ("id", str(resp.id)),
        ("respondent", _respondent_label(resp, contacts_by_id=contacts_by_id, unmasked=unmasked)),
        ("status", "completed" if resp.finished else "partial"),
        ("language", resp.language or ""),
        ("ip", _meta_ip(resp.meta, unmasked=unmasked)),
        ("submitted_at", resp.created_at.isoformat() if resp.created_at else ""),
        ("tags", "; ".join(t.name for t in tags_by_resp.get(resp.id, []))),
    ]
    for col in columns:
        record.append(
            (col["header"], tag_services._answer_to_text({}, col["qid"], answers.get(col["qid"])))
        )
    return record


def _contacts_for_responses(responses):
    """``{contact_id: Contact}`` for the contact-bearing responses (ONE prefetch query). Empty
    when no response carries a contact id."""
    contact_ids = [r.contact_id for r in responses if r.contact_id]
    if not contact_ids:
        return {}
    from apps.contacts.models import Contact

    return {
        c.id: c
        for c in Contact.objects.filter(id__in=contact_ids).prefetch_related(
            "attributes__attribute_key"
        )
    }


@login_required
@require_http_methods(["GET"])
def response_export(request, survey_id):
    """``GET /<org_slug>/surveys/<id>/responses.csv`` (or ``.json``) — stream the survey's
    responses honoring the active filters, RBAC, and PII masking.

    Requires ``export_responses`` (workspace ``read``); invisible survey → 404. The export scope
    is the SAME ``_active_filters`` set the table shows (tag/status/date/answer), via the shared
    ``tag_services.filtered_responses`` — a filtered Export matches the visible table. PII
    columns (respondent email + meta.ip) are masked for a read-only caller and raw for a write+
    caller (``pii_unmasked_for_environment`` — the rule the contacts list + share page use).
    Every CSV cell is sanitized against formula injection (``tag_services.csv_safe``). CSV is
    streamed (``StreamingHttpResponse``) so a large survey never buffers fully in memory; JSON is
    a bounded list (``?format=json``).
    """
    survey = _get_survey_or_404(survey_id)
    if not can(request.user, "export_responses", survey):
        raise Http404("Survey not found.")

    unmasked = pii_unmasked_for_environment(request.user, survey.environment)

    # Columns: one per question (full record), header = the i18n headline (fallback to qid).
    columns = [
        {"qid": q["id"], "header": _i18n(q.get("headline")) or q["id"]}
        for q in (survey.questions or [])
        if isinstance(q, dict) and "id" in q
    ]

    _state, qs = _active_filters(request, survey)
    qs = qs.order_by("created_at", "id")  # stable export order (oldest-first)

    fmt = (request.GET.get("format") or "").lower()
    is_json = fmt == "json" or request.path.endswith(".json")

    if is_json:
        return _export_json(qs, columns, survey, unmasked=unmasked)
    return _export_csv(qs, columns, survey, unmasked=unmasked)


def _export_csv(qs, columns, survey, *, unmasked):
    """Stream the filtered responses as a CSV (header + one row per response, every cell
    formula-injection-sanitized)."""
    base_headers = ["id", "respondent", "status", "language", "ip", "submitted_at", "tags"]
    header = base_headers + [c["header"] for c in columns]

    def rows():
        writer = csv.writer(_Echo())
        yield writer.writerow([tag_services.csv_safe(h) for h in header])
        # Chunked iteration: hydrate tags + contacts per chunk so memory stays bounded.
        chunk: list = []
        for resp in qs.iterator(chunk_size=200):
            chunk.append(resp)
            if len(chunk) >= 200:
                yield from _csv_chunk(writer, chunk, columns, unmasked=unmasked)
                chunk = []
        if chunk:
            yield from _csv_chunk(writer, chunk, columns, unmasked=unmasked)

    filename = f"{_safe_filename(survey.name)}-responses-{timezone.now():%Y%m%d}.csv"
    response = StreamingHttpResponse(rows(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _csv_chunk(writer, chunk, columns, *, unmasked):
    """Yield CSV lines for one chunk of responses (tags + contacts hydrated per chunk, N+1-free)."""
    tags_by_resp = tag_services.tags_for_responses([r.id for r in chunk])
    contacts_by_id = _contacts_for_responses(chunk)
    for resp in chunk:
        record = _export_record(
            resp, columns, contacts_by_id=contacts_by_id, tags_by_resp=tags_by_resp, unmasked=unmasked
        )
        yield writer.writerow([tag_services.csv_safe(v) for _k, v in record])


def _export_json(qs, columns, survey, *, unmasked):
    """Return the filtered responses as a JSON list (bounded — JSON isn't streamed). Same record
    shape as the CSV (PII masked identically); no formula sanitization (JSON is not a spreadsheet
    target)."""
    responses = list(qs[:5000])  # bound the JSON payload (documented cap)
    tags_by_resp = tag_services.tags_for_responses([r.id for r in responses])
    contacts_by_id = _contacts_for_responses(responses)
    out = []
    for resp in responses:
        record = _export_record(
            resp, columns, contacts_by_id=contacts_by_id, tags_by_resp=tags_by_resp, unmasked=unmasked
        )
        out.append({k: v for k, v in record})
    return JsonResponse({"survey": str(survey.id), "count": len(out), "responses": out})


def _safe_filename(name: str) -> str:
    """A filesystem-safe slug of the survey name for the export filename (alnum/dash/underscore)."""
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in (name or "survey"))
    return cleaned.strip("-") or "survey"
