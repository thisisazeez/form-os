"""
Server-rendered (HTMX) survey admin pages — the browser counterpart of the Survey + Response
JSON APIs (Wave 3b admin surface). These are thin Django HTML views (NOT DRF), following the
canonical pattern of ``apps.organizations.views_htmx.members_page``:

  @login_required → resolve ``request.tenant`` → RBAC via ``can()`` → services/models → render.

All authorization delegates to the single entry point ``apps.common.permissions.can()``
(rbac.md §7). The 404-vs-403 rule mirrors ``apps/surveys/builder_views.py::survey_builder_host``:
a resource the caller cannot SEE → ``Http404`` (no existence oracle); a resource they can see
but cannot ACT on → ``PermissionDenied`` (403).

List/dashboard scoping (rbac.md §8.1 "list endpoints filter, not 403"): surveys live under
``Environment → Workspace``; the dashboard + surveys list aggregate ACROSS the org's
environments but are scoped to the workspaces the caller can ``read`` — reusing the N+1-free
``workspace_access_map`` from ``apps/workspaces/access.py`` (NO per-row ``can()``).

Pages (tenant schema, under ``/<org_slug>/`` — slug stripped by TenantSlugMiddleware):
  /<org_slug>/                              dashboard_home   (repoints the tenant home)
  /<org_slug>/surveys                       surveys_list     (+ ?new=1 create-survey modal)
  /<org_slug>/surveys  (POST)               surveys_list     (create DRAFT → redirect to builder)
  /<org_slug>/surveys/<id>/share            survey_share
  /<org_slug>/surveys/<id>/results          survey_results

The Responses table + detail pages (/<org_slug>/surveys/<id>/responses[/<rid>]) live in
``apps/responses/views_htmx.py`` (the responses territory); a couple of the pure-Python answer
helpers here (``_i18n`` / ``_is_answered``) are reused there via import.
"""
from __future__ import annotations

import csv
from collections import Counter

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name

from apps.audit import services as audit
from apps.common.active_workspace import current_workspace_env_ids
from apps.common.permissions import can
from apps.responses import services as response_services
from apps.responses.models import Response
from apps.surveys import analytics, services
from apps.surveys import templates_catalog as catalog
from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey
from apps.surveys.validators import SurveyValidationError
from apps.workspaces.access import workspace_access_map
from apps.workspaces.models import Environment

# Pure-Python results/analytics folds live in ``apps.surveys.analytics`` (unit-testable without
# the HTTP/RBAC machinery). ``apps.responses`` imports ``_i18n`` / ``_is_answered`` FROM THIS
# module, so we re-export them here under their established names (signatures frozen).
_i18n = analytics.i18n
_is_answered = analytics.is_answered
_completion_rate = analytics.completion_rate
_nps_breakdown = analytics.nps_breakdown
_collect_nps_scores = analytics.collect_nps_scores
_question_results = analytics.question_results
_sparkline_points = analytics.sparkline_points

# Default definition for a freshly-created DRAFT survey (New-survey flow). An empty question
# list is a VALID survey definition (verified against survey-schema.json + the in-code
# validate_survey checks); the builder island lets the user add questions after the redirect.
# Exactly one language with default=true is required by the schema (#/$defs/surveyLanguages).
_NEW_SURVEY_NAME = "Untitled survey"
_NEW_SURVEY_LANGUAGES = [{"code": "en", "label": "English", "default": True}]


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def _routed_org(request):
    """The routed org (``request.tenant``), or None on the public schema (mirrors members_page)."""
    org = getattr(request, "tenant", None)
    if org is None or getattr(org, "schema_name", None) == get_public_schema_name():
        return None
    return org


def _readable_environment_ids(user) -> list:
    """Environment ids whose workspace the ``user`` can at least ``read`` (rbac.md §8.1).

    Built from the N+1-free ``workspace_access_map`` ONCE (no per-row ``can()``). Empty when
    the caller is a non-member or has no workspace access → empty survey lists (default-deny,
    not a 403 — the list surfaces simply show nothing/empty-state).
    """
    access_map = workspace_access_map(user)
    if not access_map:
        return []
    return list(
        Environment.objects.filter(workspace_id__in=access_map.keys()).values_list(
            "id", flat=True
        )
    )


def _get_survey_or_404(survey_id) -> Survey:
    """Load a survey with its workspace chain (for ``can()``) or 404 (mirrors builder_views)."""
    try:
        return Survey.objects.select_related(
            "environment", "environment__workspace"
        ).get(pk=survey_id)
    except (Survey.DoesNotExist, ValueError) as exc:
        raise Http404("Survey not found.") from exc


def _survey_or_visibility_404(request, survey_id) -> Survey:
    """Load the survey and gate VISIBILITY: a survey the caller cannot ``view`` → 404.

    Shared by every per-survey admin page (share/results/responses/detail). Editing actions
    layer an extra ``can()`` check on top; visibility (workspace ``read``) is the floor.
    """
    survey = _get_survey_or_404(survey_id)
    if not can(request.user, "view_survey", survey):
        # No existence oracle: invisible survey is indistinguishable from a missing one.
        raise Http404("Survey not found.")
    return survey


def _display_status(status: str) -> str:
    """Map the model SurveyStatus to the status_pill component's vocabulary.

    ``status_pill.html`` knows ``live`` / ``draft`` / ``closed`` (case-insensitive) and falls
    through to a neutral pill (showing the raw text) for anything else. We map
    ``published`` → ``live`` so a published survey reads "Live" (matching the mockups);
    ``draft`` / ``closed`` pass through unchanged, and ``paused`` falls through to the neutral
    pill rendered as "Paused" (acceptable — the component is consumed, not edited).
    """
    return "live" if status == SurveyStatus.PUBLISHED else status


# ---------------------------------------------------------------------------
# response aggregation (delegates to apps.surveys.analytics — pure-Python folds)
# ---------------------------------------------------------------------------
def _responses_per_day(survey_ids: list, days: int = 7) -> list[dict]:
    """Finished+partial response counts per day for the last ``days`` (oldest→newest).

    ONE grouped query over the readable surveys' responses; the dense zero-filled fold is done
    by ``analytics.responses_per_day`` (DB-agnostic + unit-testable). Empty ``survey_ids`` →
    a zero-filled series (no query).
    """
    if not survey_ids:
        return analytics.responses_per_day([], days=days)
    today = timezone.now().date()
    start = today - timezone.timedelta(days=days - 1)
    created_values = Response.objects.filter(
        survey_id__in=survey_ids, created_at__date__gte=start
    ).values_list("created_at", flat=True)
    return analytics.responses_per_day(created_values, days=days)


# ---------------------------------------------------------------------------
# Dashboard home (repoints the tenant home)
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET"])
def dashboard_home(request):
    """``/<org_slug>/`` — the real workspace dashboard (replaces the island-smoke tenant home).

    4 stat cards (active surveys / total responses / avg completion / avg NPS), a recent-surveys
    list, and a 7-day responses activity panel — ALL from real DB aggregates scoped to the
    caller's readable workspaces (rbac.md §8.1). A non-member sees empty stats + the empty state.
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    env_ids = current_workspace_env_ids(request)

    surveys_qs = (
        Survey.objects.filter(environment_id__in=env_ids)
        .annotate(response_count=Count("responses"))
        .order_by("-updated_at", "-created_at", "id")
        if env_ids
        else Survey.objects.none()
    )
    all_surveys = list(surveys_qs)
    survey_ids = [s.id for s in all_surveys]

    active_count = sum(1 for s in all_surveys if s.status == SurveyStatus.PUBLISHED)

    # Total + finished responses across readable surveys (counts, not row loads).
    total_responses = (
        Response.objects.filter(survey_id__in=survey_ids).count() if survey_ids else 0
    )
    finished_responses = (
        Response.objects.filter(survey_id__in=survey_ids, finished=True).count()
        if survey_ids
        else 0
    )
    avg_completion = _completion_rate(total_responses, finished_responses)

    # Avg NPS across all nps questions of readable surveys (unweighted mean of all NPS answers
    # from finished responses). "—" when there are no NPS questions/answers (brief allows it).
    # NPS question ids are scoped PER SURVEY: a question id is unique only within a survey, so a
    # global id set could mis-read another survey's same-id non-nps answer (e.g. a `rating` 1..5)
    # as an NPS score. We therefore map {survey_id: nps_qids} and collect each survey's finished
    # responses against ONLY that survey's nps question ids.
    nps_qids_by_survey = {
        s.id: {
            q["id"]
            for q in (s.questions or [])
            if isinstance(q, dict) and q.get("type") == "nps" and "id" in q
        }
        for s in all_surveys
    }
    nps_survey_ids = [sid for sid, qids in nps_qids_by_survey.items() if qids]
    avg_nps_display = "—"
    if nps_survey_ids:
        nps_scores: list[int] = []
        finished = Response.objects.filter(
            survey_id__in=nps_survey_ids, finished=True
        ).only("data", "survey_id")
        for resp in finished:
            nps_scores.extend(_collect_nps_scores([resp], nps_qids_by_survey[resp.survey_id]))
        if nps_scores:
            bd = _nps_breakdown(nps_scores)
            avg_nps_display = f"+{bd['nps']}" if bd["nps"] >= 0 else str(bd["nps"])

    # Recent surveys (top 5) decorated with the display status for the pill.
    recent = []
    for s in all_surveys[:5]:
        recent.append(
            {
                "id": s.id,
                "name": s.name,
                "display_status": _display_status(s.status),
                "response_count": s.response_count,
                "updated_at": s.updated_at,
            }
        )

    activity = _responses_per_day(survey_ids, days=7)
    activity_max = max((p["count"] for p in activity), default=0)

    context = {
        "org": org,
        "org_slug": org.slug,
        "stat_active": active_count,
        "stat_total_responses": total_responses,
        "stat_completion": f"{avg_completion}%" if avg_completion is not None else "—",
        "stat_nps": avg_nps_display,
        "recent_surveys": recent,
        "has_surveys": bool(all_surveys),
        "activity": activity,
        "activity_max": activity_max,
        "activity_points": _sparkline_points(activity),
    }
    return render(request, "dashboard/home.html", context)


# ---------------------------------------------------------------------------
# Surveys list (+ New-survey create flow)
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET", "POST"])
def surveys_list(request):
    """``/<org_slug>/surveys`` — grid of the org's surveys (scoped to readable workspaces).

    GET  — list with status filter chips (``?status=live|draft|closed``) and a "+ New survey"
           action. The legacy ``?new=1`` entry point (the topbar CTA target) now REDIRECTS to
           the survey-template gallery ``/<org_slug>/surveys/new`` (WS-14) — the gallery replaced
           the old name-it modal as the create entry point.
    POST — create a BLANK DRAFT survey via ``services.create_survey`` in a writable environment,
           then redirect to the builder host ``/<org_slug>/surveys/<id>/edit``. This is the
           gallery's "Start from scratch" path; requires workspace ``write`` somewhere.
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    if request.method == "POST":
        return _create_survey_and_redirect(request, org)

    # Legacy ?new=1 (topbar "New survey" CTA) → the template gallery.
    if request.GET.get("new") == "1":
        return redirect(f"/{org.slug}/surveys/new")

    env_ids = current_workspace_env_ids(request)
    surveys_qs = (
        Survey.objects.filter(environment_id__in=env_ids)
        .annotate(response_count=Count("responses"))
        .order_by("-updated_at", "-created_at", "id")
        if env_ids
        else Survey.objects.none()
    )

    # Status filter chip → model status. UI chips use the live/draft/closed vocabulary; the
    # "live" chip maps to the `published` status.
    chip = request.GET.get("status")
    _chip_to_status = {
        "live": SurveyStatus.PUBLISHED,
        "draft": SurveyStatus.DRAFT,
        "closed": SurveyStatus.CLOSED,
        "paused": SurveyStatus.PAUSED,
    }
    active_chip = chip if chip in _chip_to_status else "all"
    if active_chip != "all":
        surveys_qs = surveys_qs.filter(status=_chip_to_status[active_chip])

    all_for_counts = list(
        Survey.objects.filter(environment_id__in=env_ids).values_list("status", flat=True)
        if env_ids
        else []
    )
    counts = Counter(all_for_counts)

    surveys = []
    for s in surveys_qs:
        surveys.append(
            {
                "id": s.id,
                "name": s.name,
                "display_status": _display_status(s.status),
                "status": s.status,
                "response_count": s.response_count,
                "updated_at": s.updated_at,
            }
        )

    # Can the caller create a survey anywhere? (gates the New-survey CTA enablement + modal.)
    can_create = bool(_writable_environment(request.user))

    context = {
        "org": org,
        "org_slug": org.slug,
        "surveys": surveys,
        "total_count": len(all_for_counts),
        "count_all": len(all_for_counts),
        "count_live": counts.get(SurveyStatus.PUBLISHED, 0),
        "count_draft": counts.get(SurveyStatus.DRAFT, 0),
        "count_closed": counts.get(SurveyStatus.CLOSED, 0),
        "active_chip": active_chip,
        "can_create": can_create,
        "create_error": request.GET.get("create_error"),
    }
    return render(request, "surveys/list.html", context)


@login_required
@require_http_methods(["GET"])
def survey_results_index(request):
    """``/<org_slug>/results`` — the sidebar "Results" landing.

    Per-survey analytics live at ``surveys/<id>/results``; this is the index that lists the
    org's surveys (scoped to the caller's readable workspaces, rbac.md §8.1) with their response
    count + completion, each row linking to its results page. Read-only.
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    env_ids = current_workspace_env_ids(request)
    surveys_qs = (
        Survey.objects.filter(environment_id__in=env_ids)
        .annotate(
            response_count=Count("responses"),
            finished_count=Count("responses", filter=Q(responses__finished=True)),
        )
        .order_by("-response_count", "-updated_at", "id")
        if env_ids
        else Survey.objects.none()
    )
    surveys = [
        {
            "id": s.id,
            "name": s.name,
            "display_status": _display_status(s.status),
            "status": s.status,
            "response_count": s.response_count,
            "completion": _completion_rate(s.response_count, s.finished_count),
        }
        for s in surveys_qs
    ]
    return render(
        request,
        "surveys/results_index.html",
        {"org": org, "org_slug": org.slug, "surveys": surveys},
    )


def _writable_environment(user) -> Environment | None:
    """The deterministic target Environment for a new survey: the first env whose workspace
    the user can ``write``, ordered by (workspace.name, env.type) for stability.

    Uses the N+1-free access map (no per-row ``can()``), then confirms ``create_survey`` on the
    chosen env via ``can()`` (the authoritative gate). Returns None when the user cannot write
    anywhere.
    """
    access_map = workspace_access_map(user)
    writable_ws = {
        ws_id for ws_id, access in access_map.items() if access in ("write", "manage")
    }
    if not writable_ws:
        return None
    env = (
        Environment.objects.filter(workspace_id__in=writable_ws)
        .select_related("workspace")
        .order_by("workspace__name", "type", "id")
        .first()
    )
    # Authoritative confirmation via the single entry point (defense-in-depth).
    if env is not None and can(user, "create_survey", env):
        return env
    return None


def _create_survey_and_redirect(request, org):
    """POST handler for the New-survey flow: create a DRAFT then redirect to the builder.

    A caller with no writable workspace → ``PermissionDenied`` (403), mirroring
    ``members_page`` (non-admin POST → 403) and ``survey_builder_host`` (can-view-not-edit →
    403). The CTA is hidden in the UI for such users (``can_create``), so this 403 only fires
    on a stale/forged POST. On the unexpected validation failure of the fixed minimal
    definition → re-render the list with a friendly ``create_error`` message.
    """
    env = _writable_environment(request.user)
    if env is None:
        raise PermissionDenied("You do not have permission to create surveys.")

    name = (request.POST.get("name") or "").strip() or _NEW_SURVEY_NAME
    try:
        survey = services.create_survey(
            environment=env,
            name=name,
            questions=[],
            languages=_NEW_SURVEY_LANGUAGES,
            created_by_user_id=request.user.id,
        )
    except SurveyValidationError:
        return redirect(
            f"/{org.slug}/surveys?create_error=Could+not+create+the+survey."
        )
    audit.record_from_request(
        request,
        action=audit.ACTION_SURVEY_CREATED,
        target_type="survey",
        target_id=survey.id,
        metadata={"name": survey.name, "via": "htmx"},
    )
    # Land the user in the builder to add questions.
    return redirect(f"/{org.slug}/surveys/{survey.id}/edit")


# ---------------------------------------------------------------------------
# Survey template gallery (WS-14 — "Create a new survey")
# ---------------------------------------------------------------------------
def _gallery_cards(*, channel: str, industry: str, role: str, query: str) -> list[dict]:
    """The in-repo catalog filtered by the active chips + search, decorated for the cards.

    Filtering is server-side over the in-code ``catalog.CATALOG`` (no DB) — channel/industry/role
    are exact-key matches (``""`` / "all" = no filter) and ``query`` is a case-insensitive
    substring over the template name + description. Each card carries its human chip labels and a
    ``has_logic`` flag (the logic-branch indicator) so the template doesn't re-walk the
    definition.
    """
    q = (query or "").strip().lower()
    cards: list[dict] = []
    for entry in catalog.CATALOG:
        if channel and entry["channel"] != channel:
            continue
        if industry and entry["industry"] != industry:
            continue
        if role and entry["role"] != role:
            continue
        if q and q not in entry["name"].lower() and q not in entry["description"].lower():
            continue
        cards.append(
            {
                "slug": entry["slug"],
                "name": entry["name"],
                "description": entry["description"],
                "channel_label": catalog.CHANNELS.get(entry["channel"], entry["channel"]),
                "industry_label": catalog.INDUSTRIES.get(entry["industry"], entry["industry"]),
                "role_label": catalog.ROLES.get(entry["role"], entry["role"]),
                "question_count": len(entry["definition"].get("questions", [])),
                "has_logic": catalog.has_logic(entry["definition"]),
            }
        )
    return cards


def _chip_options(vocab: dict, active: str) -> list[dict]:
    """``[{key, label, active}]`` for a filter row, with an implicit leading 'All' (key '')."""
    rows = [{"key": "", "label": "All", "active": not active}]
    for key, label in vocab.items():
        rows.append({"key": key, "label": label, "active": active == key})
    return rows


@login_required
@require_http_methods(["GET", "POST"])
def survey_new(request):
    """``/<org_slug>/surveys/new`` — the "Create a new survey" template gallery (WS-14).

    GET  — render the gallery: a "Start from scratch" card + a card per in-repo catalog template,
           with Channel / Industry / Role filter chip rows and a search box (all server-side via
           ``?channel=&industry=&role=&q=`` query params, mirroring the surveys-list status chips).
           The create affordances are gated by ``can_create`` (workspace ``write`` somewhere),
           exactly like the surveys list — a read-only member sees the gallery but no create form.
    POST — create-from-template: ``template=<slug>`` → ``services.create_survey`` pre-populated
           with a DEEP COPY of that template's definition in the caller's writable env, then
           redirect to the builder. No writable workspace → 403 (mirrors the blank-create flow);
           an unknown slug → re-render the gallery with a friendly error (no survey created).
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    if request.method == "POST":
        return _create_from_template_and_redirect(request, org)

    channel = request.GET.get("channel") or ""
    industry = request.GET.get("industry") or ""
    role = request.GET.get("role") or ""
    query = request.GET.get("q") or ""
    # Ignore unknown filter keys (treat as "all") so a stray query param can't blank the gallery.
    channel = channel if channel in catalog.CHANNELS else ""
    industry = industry if industry in catalog.INDUSTRIES else ""
    role = role if role in catalog.ROLES else ""

    cards = _gallery_cards(channel=channel, industry=industry, role=role, query=query)
    can_create = bool(_writable_environment(request.user))

    context = {
        "org": org,
        "org_slug": org.slug,
        "cards": cards,
        "total_template_count": len(catalog.CATALOG),
        "channel_chips": _chip_options(catalog.CHANNELS, channel),
        "industry_chips": _chip_options(catalog.INDUSTRIES, industry),
        "role_chips": _chip_options(catalog.ROLES, role),
        "active_channel": channel,
        "active_industry": industry,
        "active_role": role,
        "query": query,
        "has_active_filter": bool(channel or industry or role or query.strip()),
        "can_create": can_create,
        "create_error": request.GET.get("create_error"),
    }
    return render(request, "surveys/new.html", context)


def _create_from_template_and_redirect(request, org):
    """POST handler for create-from-template: validate slug → create from a deep copy → builder.

    RBAC mirrors ``_create_survey_and_redirect``: no writable workspace → 403 (the gallery hides
    the create form for such users, so this only fires on a stale/forged POST). An unknown slug
    or an (unexpected) validation failure → redirect back to the gallery with a friendly error,
    creating nothing.
    """
    env = _writable_environment(request.user)
    if env is None:
        raise PermissionDenied("You do not have permission to create surveys.")

    slug = (request.POST.get("template") or "").strip()
    definition = catalog.template_definition(slug)  # deep-copied; None if unknown
    if definition is None:
        return redirect(f"/{org.slug}/surveys/new?create_error=Unknown+template.")

    try:
        survey = services.create_survey(
            environment=env,
            name=definition["name"],
            questions=definition.get("questions", []),
            languages=definition["languages"],
            logic=definition.get("logic"),
            styling=definition.get("styling"),
            welcome_card=definition.get("welcome_card") or definition.get("welcomeCard"),
            endings=definition.get("endings"),
            hidden_fields=definition.get("hidden_fields") or definition.get("hiddenFields"),
            follow_ups=definition.get("follow_ups") or definition.get("followUps"),
            blocks=definition.get("blocks"),
            created_by_user_id=request.user.id,
        )
    except SurveyValidationError:
        # The catalog is test-guarded valid, so this is unexpected — fail soft to the gallery.
        return redirect(
            f"/{org.slug}/surveys/new?create_error=Could+not+create+from+this+template."
        )

    audit.record_from_request(
        request,
        action=audit.ACTION_SURVEY_CREATED,
        target_type="survey",
        target_id=survey.id,
        metadata={"name": survey.name, "via": "htmx", "template": slug},
    )
    return redirect(f"/{org.slug}/surveys/{survey.id}/edit")


# ---------------------------------------------------------------------------
# Survey share
# ---------------------------------------------------------------------------
# Cap the personalized-links inline preview so a survey with thousands of contacts can't
# render an unbounded table (the CSV download is the bulk export).
_PERSONAL_LINKS_PREVIEW = 50


def _public_url(request, survey) -> str:
    """Absolute open-runtime URL for ``survey`` (tenant HTML family /<slug>/s/<id>)."""
    return request.build_absolute_uri(f"/{request.tenant.slug}/s/{survey.id}")


def _contact_label(contact, *, unmasked: bool) -> tuple[str, str]:
    """``(name, email)`` for a contact, reusing the contacts surface's display helpers.

    Single source of truth for identity/email resolution — we import the contacts app's
    ``attr_value_map`` / ``display_name`` / ``EMAIL_KEY`` rather than re-deriving them. PII
    (the email value) is MASKED for read-only callers and unmasked only on workspace ``write``,
    mirroring the contacts module's ``pii_unmasked_for_environment`` rule (rbac.md §11) so the
    share page never exposes a WIDER PII surface than the contacts list does to the same user.
    The display ``name`` and the personalized URL (an opaque token, not PII) are always shown.
    """
    from apps.contacts.views_htmx import EMAIL_KEY, attr_value_map, display_name

    attr_map = attr_value_map(contact, unmasked=unmasked)
    name = display_name(attr_map, contact)
    email = attr_map.get(EMAIL_KEY, {}).get("value", "") or ""
    return name, email


def _personal_link_rows(request, survey, *, limit=None) -> list[dict]:
    """Build ``[{name, email, token, personal_url, consumed}]`` rows for the survey's links.

    Resolves each link's contact (by-id; ADR-008 means ``contact_id`` is not an FK) in ONE
    prefetch-friendly pass. ``limit`` caps the rows (inline preview); ``None`` = all (CSV).
    Email PII is masked for read-only callers (write gate via ``pii_unmasked_for_environment``).
    """
    from apps.contacts.models import Contact
    from apps.contacts.scoping import pii_unmasked_for_environment

    unmasked = pii_unmasked_for_environment(request.user, survey.environment)

    links_qs = response_services.links_for_survey(survey)
    if limit is not None:
        links_qs = links_qs[:limit]
    links = list(links_qs)

    contact_ids = [link.contact_id for link in links if link.contact_id]
    contacts = {
        c.id: c
        for c in Contact.objects.filter(id__in=contact_ids).prefetch_related(
            "attributes__attribute_key"
        )
    }
    base_url = _public_url(request, survey)

    rows: list[dict] = []
    for link in links:
        contact = contacts.get(link.contact_id)
        if contact is not None:
            name, email = _contact_label(contact, unmasked=unmasked)
        else:
            # Generic single-use link (no contact) or a since-erased contact.
            name, email = "Anonymous link", ""
        rows.append(
            {
                "name": name,
                "email": email,
                "token": link.token,
                "personal_url": response_services.personal_url(base_url, link),
                "consumed": link.consumed_at is not None,
            }
        )
    return rows


def _share_context(request, survey) -> dict:
    """Shared render context for the share page (GET + post-mint re-render)."""
    public_url = _public_url(request, survey)
    # Segments available to target (this survey's environment), newest-meaningful order.
    from apps.segments.models import Segment

    segments = list(
        Segment.objects.filter(environment_id=survey.environment_id).order_by("title")
    )
    rows = _personal_link_rows(request, survey, limit=_PERSONAL_LINKS_PREVIEW)
    total_links = response_services.links_for_survey(survey).count()
    can_mint = can(request.user, "edit_survey", survey)

    return {
        "org_slug": request.tenant.slug,
        "survey": survey,
        "survey_display_status": _display_status(survey.status),
        "public_url": public_url,
        "is_published": survey.status == SurveyStatus.PUBLISHED,
        "single_use": survey.single_use,
        "close_on": survey.close_on or {},
        "segments": segments,
        "personal_links": rows,
        "personal_links_total": total_links,
        "personal_links_truncated": total_links > len(rows),
        "personal_links_preview": _PERSONAL_LINKS_PREVIEW,
        "can_mint": can_mint,
    }


@login_required
@require_http_methods(["GET"])
def survey_share(request, survey_id):
    """``/<org_slug>/surveys/<id>/share`` — public link, web embed, QR, email snippet, and
    personalized per-contact links.

    The public link is the open runtime URL ``/<org_slug>/s/<survey.id>`` (ADR-009: open
    public links need no SurveyLink row; the survey id IS the link). Personalized links are
    real ``SurveyLink`` rows minted here and resolved by the existing public runtime path
    (``public_services.resolve_link``). Visibility = ``view_survey`` (404 if not visible);
    minting layers ``edit_survey`` on top (enforced in ``survey_mint_links``).
    """
    survey = _survey_or_visibility_404(request, survey_id)
    return render(request, "surveys/share.html", _share_context(request, survey))


@login_required
@require_http_methods(["POST"])
def survey_mint_links(request, survey_id):
    """POST ``/<org_slug>/surveys/<id>/share/links`` — mint personalized links for contacts.

    Targets either a whole segment (``?segment=<id>``/form field) or ALL contacts in the
    survey's environment (``target=all``). Requires ``edit_survey`` (write) — a read-only
    member POSTing → 403 (mirrors the new-survey flow). Re-renders the share page with the
    updated link list (idempotent minting skips contacts that already have a live link).
    """
    survey = _survey_or_visibility_404(request, survey_id)
    if not can(request.user, "edit_survey", survey):
        raise PermissionDenied("You do not have permission to generate links.")

    contact_ids = _resolve_target_contacts(request, survey)
    response_services.mint_links_for_contacts(survey, contact_ids)

    return render(request, "surveys/share.html", _share_context(request, survey))


def _resolve_target_contacts(request, survey) -> list:
    """Resolve the contact ids to mint links for from the POST (segment id or 'all')."""
    from apps.contacts.models import Contact

    target = request.POST.get("target", "")
    segment_id = (request.POST.get("segment") or "").strip()

    if target == "segment" and segment_id:
        from apps.segments.models import Segment
        from apps.segments.services import evaluate

        try:
            segment = Segment.objects.get(
                pk=segment_id, environment_id=survey.environment_id
            )
        except (Segment.DoesNotExist, ValueError):
            return []
        return [c.id for c in evaluate(segment)]

    # Default / target=all: every contact in the survey's environment.
    return list(
        Contact.objects.filter(environment_id=survey.environment_id).values_list(
            "id", flat=True
        )
    )


def _csv_safe(value: str) -> str:
    """Neutralize CSV/formula injection: a cell starting with =,+,-,@ (or a leading tab/CR) is
    prefixed with a single quote so a spreadsheet treats it as text, not a formula. Contact
    names are attacker-influenced free text, so we sanitize before writing."""
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@login_required
@require_http_methods(["GET"])
def survey_links_csv(request, survey_id):
    """GET ``/<org_slug>/surveys/<id>/share/links.csv`` — download all personalized links.

    CSV columns: ``name,email,personal_url``. Visibility floor = ``view_survey`` (read), the
    same as the share page; the ``email`` PII column is masked for read-only callers (write
    gate, via ``_personal_link_rows``). Streams ALL links (no preview cap). Cell values are
    sanitized against CSV/formula injection.
    """
    survey = _survey_or_visibility_404(request, survey_id)
    rows = _personal_link_rows(request, survey, limit=None)

    filename = f"{slugify(survey.name) or 'survey'}-personal-links.csv"
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(resp)
    writer.writerow(["name", "email", "personal_url"])
    for row in rows:
        writer.writerow(
            [_csv_safe(row["name"]), _csv_safe(row["email"]), _csv_safe(row["personal_url"])]
        )
    return resp


@login_required
@require_http_methods(["GET"])
def survey_qr(request, survey_id):
    """GET ``/<org_slug>/surveys/<id>/share/qr.<png|svg>`` — QR code for the public link.

    Encodes the absolute open-runtime URL. ``?format=svg`` (or the ``.svg`` route) returns
    crisp vector; default is PNG. Visibility floor = ``view_survey`` (read). Generated with
    ``segno`` (pure-Python, no Pillow).
    """
    import io

    import segno

    survey = _survey_or_visibility_404(request, survey_id)
    public_url = _public_url(request, survey)

    fmt = (request.GET.get("format") or "png").lower()
    qr = segno.make(public_url, error="m")
    buf = io.BytesIO()
    if fmt == "svg":
        qr.save(buf, kind="svg", scale=6)
        content_type = "image/svg+xml"
        ext = "svg"
    else:
        qr.save(buf, kind="png", scale=8, border=2)
        content_type = "image/png"
        ext = "png"

    filename = f"{slugify(survey.name) or 'survey'}-qr.{ext}"
    resp = HttpResponse(buf.getvalue(), content_type=content_type)
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


# ---------------------------------------------------------------------------
# Results / analytics
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET"])
def survey_results(request, survey_id):
    """``/<org_slug>/surveys/<id>/results`` — KPI cards + per-question aggregates.

    Requires ``view_analytics`` (workspace ``read``); an invisible survey → 404. Aggregates are
    a pure-Python fold over the survey's responses (no chart libs); CSS bars render the
    distributions.
    """
    survey = _get_survey_or_404(survey_id)
    # view_analytics is workspace `read` (same floor as view_survey). Invisible → 404.
    if not can(request.user, "view_analytics", survey):
        raise Http404("Survey not found.")

    # ONE query loads every response (partial + finished). Everything below is a pure-Python
    # fold over this in-memory list — no per-question or per-funnel-step DB round-trips.
    # ``created_at`` powers the activity sparkline; ``started_at``/``finished_at`` the
    # time-to-complete KPI; ``data``/``finished`` the funnel + per-question summaries.
    responses = list(
        Response.objects.filter(survey=survey).only(
            "data", "finished", "survey_id", "created_at", "started_at", "finished_at"
        )
    )
    total = len(responses)
    finished = sum(1 for r in responses if r.finished)
    completion = _completion_rate(total, finished)

    # NPS across this survey's nps questions (for the KPI strip).
    nps_qids = {
        q["id"]
        for q in (survey.questions or [])
        if isinstance(q, dict) and q.get("type") == "nps" and "id" in q
    }
    nps_scores = _collect_nps_scores((r for r in responses if r.finished), nps_qids)
    nps_summary = _nps_breakdown(nps_scores)

    # Pre-format the KPI NPS value (template can't do "+%d" formatting cleanly).
    if nps_qids and nps_summary["nps"] is not None:
        nps_display = f"+{nps_summary['nps']}" if nps_summary["nps"] >= 0 else str(nps_summary["nps"])
    else:
        nps_display = "—"

    question_results = _question_results(survey, responses)
    funnel = analytics.completion_funnel(survey, responses)
    ttc = analytics.time_to_complete(responses)

    # Responses-over-time sparkline (reuses the dashboard's inline-SVG pattern). Fold the
    # already-loaded responses' ``created_at`` — NO extra query.
    activity = analytics.responses_per_day([r.created_at for r in responses], days=7)
    activity_max = max((p["count"] for p in activity), default=0)

    context = {
        "org_slug": request.tenant.slug,
        "survey": survey,
        "survey_display_status": _display_status(survey.status),
        "total_responses": total,
        "finished_responses": finished,
        "completion": f"{completion}%" if completion is not None else "—",
        "time_to_complete": ttc,
        "nps_summary": nps_summary,
        "nps_display": nps_display,
        "has_nps": bool(nps_qids),
        "question_results": question_results,
        "funnel": funnel,
        "activity": activity,
        "activity_max": activity_max,
        "activity_points": _sparkline_points(activity),
    }
    return render(request, "surveys/results.html", context)
