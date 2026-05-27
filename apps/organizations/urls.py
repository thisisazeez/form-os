"""
Organization URLs — split into PUBLIC-schema and TENANT-prefixed patterns
(contracts/openapi.yaml — Organizations tag).

``public_urlpatterns`` mount in config/urls_public.py under ``api/v1/`` (no org slug). They
are nested under the whitelisted ``auth/`` prefix because TenantSlugMiddleware
(apps/common/middleware.py — OUTSIDE this producer's territory) treats any first
``/api/v1/<seg>`` other than ``auth`` as an org slug → 404. See the BLOCKING openapi
deviation note in SUMMARY + FOLLOWUPS.md.
  POST api/v1/auth/organizations       → OrganizationCreateView   (openapi has no path; new)
  POST api/v1/auth/invitations/accept  → InvitationAcceptView      (openapi: /api/v1/invitations/accept)

``urlpatterns`` (tenant) mount in config/urls.py under ``api/v1/`` AFTER TenantSlugMiddleware
strips the org slug — so request ``/api/v1/<slug>/org`` arrives here as ``api/v1/org``:
  GET/PATCH    api/v1/org                                  → OrgView
  GET          api/v1/members                              → MemberListView
  PATCH/DELETE api/v1/members/<uuid:membership_id>         → MemberDetailView
  GET/POST     api/v1/invitations                          → InvitationListCreateView
  DELETE       api/v1/invitations/<uuid:invitation_id>     → InvitationDetailView
"""
from django.urls import path

from . import views, views_htmx

app_name = "organizations"

# Public-schema routes (no org slug) — included by config/urls_public.py. Nested under
# `auth/` per the middleware deviation documented above.
public_urlpatterns = [
    path("auth/organizations", views.OrganizationCreateView.as_view(), name="org-create"),
    path(
        "auth/invitations/accept",
        views.InvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
]

# Tenant-prefixed routes — included by config/urls.py (slug already stripped).
urlpatterns = [
    path("org", views.OrgView.as_view(), name="org-detail"),
    path("members", views.MemberListView.as_view(), name="member-list"),
    path(
        "members/<uuid:membership_id>",
        views.MemberDetailView.as_view(),
        name="member-detail",
    ),
    path("invitations", views.InvitationListCreateView.as_view(), name="invitation-list"),
    path(
        "invitations/<uuid:invitation_id>",
        views.InvitationDetailView.as_view(),
        name="invitation-detail",
    ),
]

# --- HTMX (server-rendered) pages ------------------------------------------
# Public-schema HTMX pages — included by config/urls_public.py under `auth/` (middleware
# deviation, as above). Namespace: `org_public_htmx`.
#   /auth/organizations/new       GET form / POST create org
#   /auth/invitations/accept      GET show + accept / POST accept
public_htmx_urlpatterns = [
    path(
        "auth/organizations/new",
        views_htmx.create_organization_page,
        name="org-create",
    ),
    path(
        "auth/invitations/accept",
        views_htmx.accept_invitation_page,
        name="accept-invitation",
    ),
]

# Tenant HTMX pages — included by config/urls.py (slug already stripped). Namespace:
# `organizations_htmx`.
#   /<org_slug>/members           GET list + invite form (owner/admin POST)
htmx_urlpatterns = [
    path("members", views_htmx.members_page, name="members"),
]
