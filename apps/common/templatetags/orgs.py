"""
Template assignment tag for the sidebar **organization switcher** —
``{% user_organizations as my_orgs %}`` (used by templates/_layouts/app_shell.html).

A user can belong to many orgs (``Membership`` is unique only per (user, org)); the shell's
switcher lists them so the user can jump between orgs. This tag returns that list for the
CURRENT user, flagging the routed org (``request.tenant``) as current.

Public-schema read: ``Membership`` is a SHARED_APPS model (ADR-002), so we resolve it under
``schema_context(public)`` regardless of the active tenant schema — same pattern as
``apps.organizations.services.first_active_org`` and ``apps.common.permissions``.

Usage:
    {% load orgs %}
    {% user_organizations as my_orgs %}
    {% for o in my_orgs %}<a href="/{{ o.slug }}/">{{ o.name }} ({{ o.role }}){% if o.is_current %} ✓{% endif %}</a>{% endfor %}

Fail-soft: anonymous (or no request) → empty list, so the shell never errors over a UI affordance.
"""
from __future__ import annotations

from django import template
from django_tenants.utils import get_public_schema_name, schema_context

register = template.Library()


@register.simple_tag(takes_context=True)
def user_organizations(context):
    """The current user's active-membership orgs (ordered by name) for the switcher.

    Each item is a dict ``{slug, name, role, is_current}``. ``role`` is the human-readable
    org role ("Owner"/"Admin"/"Member"); ``is_current`` marks the routed org. Returns ``[]``
    for an anonymous user or a missing request.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    current_slug = getattr(getattr(request, "tenant", None), "slug", None)

    # Local imports avoid a module-load cycle (common ↔ organizations).
    from apps.organizations.enums import MembershipStatus
    from apps.organizations.models import Membership

    with schema_context(get_public_schema_name()):
        memberships = (
            Membership.objects.filter(user=user, status=MembershipStatus.ACTIVE)
            .select_related("organization")
            .order_by("organization__name", "organization__slug")
        )
        return [
            {
                "slug": m.organization.slug,
                "name": m.organization.name,
                "role": m.get_role_display(),
                "is_current": m.organization.slug == current_slug,
            }
            for m in memberships
        ]


@register.simple_tag(takes_context=True)
def current_workspace_name(context):
    """The caller's current workspace name in the routed org (for the switcher trigger), or ""."""
    from apps.common.active_workspace import current_workspace

    ws = current_workspace(context.get("request"))
    return ws.name if ws else ""


@register.simple_tag(takes_context=True)
def org_workspaces(context):
    """Readable workspaces in the ROUTED org for the switcher's "workspaces" view.

    Each item: ``{id, name, access, is_current}`` — ``access`` is the human-readable level
    ("Manage"/"Write"/"Read"); ``is_current`` flags the active workspace. Empty for anonymous
    users or callers with no workspace access. N+1-free (apps.workspaces.access).
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    from apps.common.active_workspace import current_workspace
    from apps.workspaces.access import readable_workspaces_with_access

    workspaces, access_map = readable_workspaces_with_access(user)
    if not workspaces:
        return []
    cur = current_workspace(request)
    cur_id = cur.id if cur else None
    return [
        {
            "id": ws.id,
            "name": ws.name,
            "access": (access_map.get(ws.id) or "read").title(),
            "is_current": ws.id == cur_id,
        }
        for ws in workspaces
    ]
