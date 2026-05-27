"""
Segments + segment-evaluation URLs (contracts/openapi.yaml — Segments tag).

TENANT-prefixed — included by config/urls.py under ``api/v1/`` AFTER TenantSlugMiddleware
strips the org slug. So ``/api/v1/<slug>/environments/<env>/segments`` arrives here as
``api/v1/environments/<env>/segments`` (mirrors openapi ``/api/v1/{org_slug}/...``):

  GET/POST     api/v1/environments/<uuid:environment_id>/segments         → SegmentListCreateView
  PUT/DELETE   api/v1/segments/<uuid:segment_id>                          → SegmentDetailView

Additions ([ADD] — required by the producer brief; not in openapi.yaml):
  GET          api/v1/segments/<uuid:segment_id>/contacts                 → SegmentContactsView (EVALUATION)
  POST         api/v1/environments/<uuid:environment_id>/segments/preview → SegmentPreviewView (unsaved filter)

Path ordering: the static-suffix routes (``/preview``, ``/contacts``) are listed before the
UUID-detail routes so they match first (Django tries patterns top-to-bottom).
"""
from django.urls import path

from . import views

app_name = "segments"

urlpatterns = [
    # [ADD] preview an unsaved filter (static suffix — before the bare list route is fine; the
    # list route has no trailing segment so they're unambiguous).
    path(
        "environments/<uuid:environment_id>/segments/preview",
        views.SegmentPreviewView.as_view(),
        name="segment-preview",
    ),
    path(
        "environments/<uuid:environment_id>/segments",
        views.SegmentListCreateView.as_view(),
        name="segment-list",
    ),
    # [ADD] segment evaluation — matched contacts (static suffix before the bare detail route).
    path(
        "segments/<uuid:segment_id>/contacts",
        views.SegmentContactsView.as_view(),
        name="segment-contacts",
    ),
    path(
        "segments/<uuid:segment_id>",
        views.SegmentDetailView.as_view(),
        name="segment-detail",
    ),
]
