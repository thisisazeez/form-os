"""
Server-rendered (HTMX) audit-log viewer — a Settings tab.

Owner/admin only (RBAC action ``view_audit_log`` — apps.common.permissions.can()). The append-
only AuditLog (models.md §2.16) is written from the mutation surfaces (apps.audit.services.record);
this page READS it.

Page (TENANT schema, mounted by config/urls.py under /<org_slug>/):
  /<org_slug>/settings/audit-log  GET — paginated, filterable list (actor / action / target / time).

Filters (all optional querystring params, AND-combined):
  ?action=<substring>   — case-insensitive substring on the action string.
  ?target=<type>        — exact target_type match.
  ?actor=<substring>    — case-insensitive substring on actor name/email (resolved via hydration).
Actors are hydrated in ONE public-schema query (apps.common.hydration) — no N+1.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name

from apps.common.hydration import hydrate_users
from apps.common.permissions import can

from .models import AuditLog

_PAGE_SIZE = 25


@login_required
@require_http_methods(["GET"])
def audit_log_page(request):
    """``/<org_slug>/settings/audit-log`` — paginated, filterable audit list (owner/admin)."""
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    # RBAC: owner/admin only (view_audit_log). Plain members / non-members → 403 page.
    if not can(request.user, "view_audit_log"):
        return render(request, "organizations/not_a_member.html", {"org": org}, status=403)

    action_q = (request.GET.get("action") or "").strip()
    target_q = (request.GET.get("target") or "").strip()
    actor_q = (request.GET.get("actor") or "").strip()

    qs = AuditLog.objects.all().order_by("-created_at")
    if action_q:
        qs = qs.filter(action__icontains=action_q)
    if target_q:
        qs = qs.filter(target_type=target_q)

    paginator = Paginator(qs, _PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    # Hydrate actor users for the current page in ONE public-schema query (no N+1).
    entries = hydrate_users(list(page.object_list), fields=("actor_user_id",))

    # Actor filter is applied AFTER hydration (it filters on the resolved name/email). Kept
    # post-hydration because actor identity lives in the public User, not on the tenant row.
    if actor_q:
        lowered = actor_q.lower()
        entries = [e for e in entries if _actor_matches(e, lowered)]

    # Distinct target types present (for the filter dropdown) — cheap DISTINCT query.
    target_types = list(
        AuditLog.objects.order_by("target_type")
        .values_list("target_type", flat=True)
        .distinct()
    )

    return render(
        request,
        "settings/audit_log.html",
        {
            "org": org,
            "section": "audit-log",
            "entries": entries,
            "page": page,
            "paginator": paginator,
            "target_types": [t for t in target_types if t],
            "filters": {"action": action_q, "target": target_q, "actor": actor_q},
        },
    )


def _actor_matches(entry, lowered: str) -> bool:
    actor = getattr(entry, "actor_user_obj", None)
    if actor is None:
        return "system" in lowered  # let "system" match the null-actor rows.
    name = (getattr(actor, "name", "") or "").lower()
    email = (getattr(actor, "email", "") or "").lower()
    return lowered in name or lowered in email


def _routed_org(request):
    org = getattr(request, "tenant", None)
    if org is None or getattr(org, "schema_name", None) == get_public_schema_name():
        return None
    return org
