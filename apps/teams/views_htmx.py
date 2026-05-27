"""
Server-rendered (HTMX) Teams & access-control page — the browser counterpart of the Teams
JSON API (apps.teams.views). The page renders the master/detail layout; the interactive
writes (create team, grant/revoke a team's workspace access, add/remove a team member) are
HTMX endpoints in THIS module that delegate to ``apps.teams.services`` — the SAME service
layer the DRF API uses (no business logic duplicated here; mirrors
apps.segments.views_htmx + apps.organizations.views_htmx.members_page).

Pages / endpoints (TENANT schema, config/urls.py under /<org_slug>/ — slug stripped by
TenantSlugMiddleware, ADR-001):
  GET  /<org_slug>/teams                         → teams_page    (list + members + matrix)
  POST /<org_slug>/teams                         → teams_page    (create team → redirect)
  POST /<org_slug>/teams/<id>/access             → workspace_access (HTMX: grant/revoke; returns
                                                    the segmented-control row partial)
  POST /<org_slug>/teams/<id>/members            → team_add_member (HTMX: add → members partial)
  POST /<org_slug>/teams/<id>/members/<uid>/remove → team_remove_member (HTMX → members partial)

RBAC mirrors apps.teams.views + apps.common.permissions.can() (the single entry point,
rbac.md §7 — no surface invents its own checks):
  - The page itself requires the caller be an active org member (else 403 — same shape as
    members_page's non-member path).
  - Team VISIBILITY is filtered exactly like the API: org owner/admin see every team; a plain
    member sees only the teams they belong to (can(user, "view_team", team)).
  - create_team / grant_workspace_access → org owner/admin (can()).
  - manage_team_membership → org owner/admin OR team-admin of THIS team (can(), object-scoped).
  - Removing/lowering the LAST delegated ``manage`` path to a workspace is refused at the
    service layer (services.LastManagePathError → surfaced inline in the row partial).
  - The selected team is taken from ?team_id=<uuid> (default: the first visible team by name);
    an unknown/!visible team_id falls back to the first visible team.

Member lists use hydrate_users("user_id") — ONE public-schema query (ADR-002 / models.md
PART A); the template reads the hydrated user_obj, never User.objects.get per row.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name, schema_context

from apps.common.permissions import can
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership
from apps.teams import services
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.models import Workspace


@login_required
@require_http_methods(["GET", "POST"])
def teams_page(request):
    """``/<org_slug>/teams`` — Teams list + selected-team members + workspace-access matrix.

    GET  — render the master/detail page (``?team_id`` selects; ``?new=1`` opens the create
           modal). POST — create a team via ``services.create_team`` (org owner/admin), then
           redirect to ``?team_id=<new>`` so it lands selected (mirrors the New-survey flow).
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")

    membership = _active_membership(request.user, org)
    if membership is None:
        # Not a member of this org → same 403 page as the members surface.
        return render(request, "organizations/not_a_member.html", {"org": org}, status=403)

    is_admin = membership.role in (OrgRole.OWNER, OrgRole.ADMIN)

    create_error = request.GET.get("create_error")
    if request.method == "POST":
        return _create_team_and_redirect(request, org)

    # Visible teams (mirror TeamListCreateView.get): owner/admin see all; member sees own.
    teams_qs = Team.objects.annotate(member_count=Count("memberships")).order_by("name", "id")
    if not is_admin:
        my_team_ids = TeamMembership.objects.filter(user_id=request.user.id).values_list(
            "team_id", flat=True
        )
        teams_qs = teams_qs.filter(id__in=my_team_ids)
    teams = list(teams_qs)

    # Resolve the selected team from ?team_id, defaulting to the first visible team. An
    # unknown / not-visible id silently falls back (no existence oracle for hidden teams).
    selected = _select_team(teams, request.GET.get("team_id"))

    members = []
    matrix = []
    candidates = []
    can_manage_membership = False
    if selected is not None:
        # Hydrate the selected team's members to public Users in ONE query (ADR-002).
        members = list(
            TeamMembership.objects.filter(team=selected)
            .order_by("created_at")
            .hydrate_users("user_id")
        )
        matrix = _access_matrix(selected)
        # manage_team_membership is object-scoped (org owner/admin OR team-admin of THIS team).
        can_manage_membership = can(request.user, "manage_team_membership", selected)
        if can_manage_membership:
            candidates = _candidate_members(org, selected)

    return render(
        request,
        "teams/teams.html",
        {
            "org": org,
            "membership": membership,
            "is_admin": is_admin,
            "teams": teams,
            "selected_team": selected,
            "members": members,
            "matrix": matrix,
            # can(grant_workspace_access) is the org-level owner/admin gate; the matrix pills are
            # interactive only when the caller may actually change access (else read-only display).
            "can_manage_access": can(request.user, "grant_workspace_access", None),
            # Org members not yet on the team — the Add-member modal's <select> candidates.
            "can_manage_membership": can_manage_membership,
            "candidates": candidates,
            "access_levels": WorkspaceAccess.values,  # ["read","write","manage"]
            "create_error": create_error,
            "show_new_modal": request.GET.get("new") == "1",
        },
    )


# ---------------------------------------------------------------------------
# Create team (modal → POST on the page; redirect to the new team selected)
# ---------------------------------------------------------------------------
def _create_team_and_redirect(request, org):
    """POST handler for the New-team flow: create via the service, then redirect selected.

    A caller who is not org owner/admin → ``PermissionDenied`` (403), mirroring members_page's
    non-admin POST path. The CTA is hidden for such users, so this only fires on a stale/forged
    POST. A duplicate-name (case-insensitive) or empty name → re-render the page with a friendly
    ``create_error`` (the create modal stays useful).
    """
    if not can(request.user, "create_team", None):
        raise PermissionDenied("You do not have permission to create teams.")

    name = (request.POST.get("name") or "").strip()
    try:
        team = services.create_team(name=name, created_by_user_id=request.user.id)
    except ValidationError as exc:
        msg = exc.messages[0] if exc.messages else "Could not create the team."
        from urllib.parse import quote

        return redirect(f"/{org.slug}/teams?create_error={quote(msg)}")
    return redirect(f"/{org.slug}/teams?team_id={team.id}")


# ---------------------------------------------------------------------------
# Workspace access matrix (HTMX: grant/revoke → return the row's segmented control)
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["POST"])
def workspace_access(request, team_id):
    """``POST /<org_slug>/teams/<id>/access`` — set this team's access on a workspace (HTMX).

    Body: ``workspace_id`` + ``access`` ∈ {none, read, write, manage}. ``none`` REVOKES (deletes
    the WorkspaceTeam row); a real level GRANTS/UPSERTS. RBAC: ``grant_workspace_access`` (org
    owner/admin only — rbac.md §5). Returns the single matrix ROW partial reflecting the new
    state (``hx-swap=outerHTML`` on the radiogroup); a guard/validation failure re-renders the
    SAME row at its UNCHANGED state with an inline error (no full reload).
    """
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")
    if _active_membership(request.user, org) is None:
        raise PermissionDenied("Not a member of this organization.")
    if not can(request.user, "grant_workspace_access", None):
        raise PermissionDenied("You do not have permission to grant workspace access.")

    team = _get_team_or_404(team_id)
    workspace = _get_workspace_or_404(request.POST.get("workspace_id"))
    access = request.POST.get("access", "")

    error = None
    try:
        if access == "none":
            services.revoke_workspace_access(team=team, workspace_id=workspace.id)
        elif access in WorkspaceAccess.values:
            services.grant_workspace_access(
                team=team, workspace_id=workspace.id, access=access
            )
        else:
            error = "Invalid access level."
    except services.LastManagePathError as exc:
        error = exc.messages[0] if exc.messages else "Cannot change access."
    except ValidationError as exc:
        error = exc.messages[0] if exc.messages else "Could not change access."

    # Re-read the team's CURRENT access for this workspace (post-change, or unchanged on error).
    current = (
        WorkspaceTeam.objects.filter(team=team, workspace=workspace)
        .values_list("access", flat=True)
        .first()
    )
    return render(
        request,
        "teams/_matrix_row.html",
        {
            "org_slug": org.slug,
            "selected_team": team,
            "row": {"workspace": workspace, "access": current},
            "access_levels": WorkspaceAccess.values,
            "can_manage_access": True,
            "row_error": error,
        },
    )


# ---------------------------------------------------------------------------
# Team membership (HTMX: add / remove → return the members table partial)
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["POST"])
def team_add_member(request, team_id):
    """``POST /<org_slug>/teams/<id>/members`` — add an existing org member to the team (HTMX).

    Body: ``user_id`` (a PUBLIC User PK of an ACTIVE org member). RBAC:
    ``manage_team_membership`` (org owner/admin OR team-admin of THIS team). Returns the members
    card partial (table + Add control) so the swap reflects the new member without a reload.
    """
    org, team = _membership_guard(request, team_id)
    error = None
    user_id = request.POST.get("user_id")
    if not user_id:
        error = "Select a member to add."
    else:
        try:
            services.add_team_member(organization=org, team=team, user_id=user_id)
        except ValidationError as exc:
            error = exc.messages[0] if exc.messages else "Could not add member."
    return _members_partial(request, org, team, error=error)


@login_required
@require_http_methods(["POST"])
def team_remove_member(request, team_id, user_id):
    """``POST /<org_slug>/teams/<id>/members/<uid>/remove`` — remove a member (HTMX).

    HTML forms cannot issue DELETE, so removal is a POST (mirrors members_page's
    ``op=change_role`` POST convention). RBAC: ``manage_team_membership``. Idempotent: removing
    an absent member is a no-op. Returns the refreshed members card partial.
    """
    org, team = _membership_guard(request, team_id)
    services.remove_team_member(team=team, user_id=user_id)
    return _members_partial(request, org, team)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _membership_guard(request, team_id):
    """Shared guard for the membership endpoints: resolve org + team, enforce can()."""
    org = _routed_org(request)
    if org is None:
        raise Http404("Organization not resolved.")
    if _active_membership(request.user, org) is None:
        raise PermissionDenied("Not a member of this organization.")
    team = _get_team_or_404(team_id)
    if not can(request.user, "manage_team_membership", team):
        raise PermissionDenied("You do not have permission to manage this team's membership.")
    return org, team


def _members_partial(request, org, team, *, error=None):
    """Render the members card partial for an HTMX swap (table body + Add control + candidates)."""
    members = list(
        TeamMembership.objects.filter(team=team)
        .order_by("created_at")
        .hydrate_users("user_id")
    )
    return render(
        request,
        "teams/_members_card.html",
        {
            "org": org,
            "selected_team": team,
            "members": members,
            "candidates": _candidate_members(org, team),
            "can_manage_membership": True,
            "member_error": error,
        },
    )


def _candidate_members(org, team):
    """Org members (ACTIVE, public schema) NOT already on ``team`` — the Add-member candidates.

    ONE public-schema query for the org's memberships (with their User joined), minus the
    team's existing ``user_id`` set (ONE tenant query). Returns dicts {id, label} for a
    <select> (label = name or email). No per-row User lookups (ADR-002).
    """
    existing = set(
        TeamMembership.objects.filter(team=team).values_list("user_id", flat=True)
    )
    with schema_context(get_public_schema_name()):
        rows = (
            Membership.objects.filter(organization=org, status=MembershipStatus.ACTIVE)
            .select_related("user")
            .order_by("user__name", "user__email")
        )
        candidates = []
        for m in rows:
            if m.user_id in existing:
                continue
            label = (m.user.name or "").strip() or m.user.email
            candidates.append({"id": m.user_id, "label": label})
    return candidates


def _get_team_or_404(team_id):
    """Load a Team by id (tenant schema), else 404 (mirrors the API's _get_team_or_404)."""
    try:
        return Team.objects.get(pk=team_id)
    except (Team.DoesNotExist, ValueError, ValidationError) as exc:
        raise Http404("Team not found.") from exc


def _get_workspace_or_404(workspace_id):
    """Load a Workspace by id (tenant schema), else 404."""
    try:
        return Workspace.objects.get(pk=workspace_id)
    except (Workspace.DoesNotExist, ValueError, ValidationError) as exc:
        raise Http404("Workspace not found.") from exc


def _select_team(teams, team_id):
    """Pick the selected Team: the ?team_id match if it's visible, else the first team."""
    if not teams:
        return None
    if team_id:
        for team in teams:
            if str(team.id) == str(team_id):
                return team
    return teams[0]


def _access_matrix(team):
    """Build matrix rows = every Workspace, with the team's current access (or None).

    Returns a list of dicts {workspace, access} where access ∈ {None,"read","write","manage"}.
    "None" means no WorkspaceTeam row exists for (team, workspace). ONE WorkspaceTeam query.
    """
    granted = dict(
        WorkspaceTeam.objects.filter(team=team).values_list("workspace_id", "access")
    )
    rows = []
    for workspace in Workspace.objects.order_by("name", "id"):
        rows.append({"workspace": workspace, "access": granted.get(workspace.id)})
    return rows


def _routed_org(request):
    org = getattr(request, "tenant", None)
    if org is None or getattr(org, "schema_name", None) == get_public_schema_name():
        return None
    return org


def _active_membership(user, org):
    """The caller's ACTIVE Membership for the routed org (PUBLIC schema), or None.

    Mirrors apps.organizations.views_htmx.members_page — Membership lives in the public schema
    (ADR-002), so we read it under schema_context(public).
    """
    if not user.is_authenticated or org is None:
        return None
    with schema_context(get_public_schema_name()):
        return (
            Membership.objects.select_related("organization")
            .filter(user=user, organization=org, status=MembershipStatus.ACTIVE)
            .first()
        )
