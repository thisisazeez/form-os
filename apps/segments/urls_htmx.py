"""
Segments HTMX (server-rendered admin) URLs — the browser pages, distinct from the DRF API in
``apps/segments/urls.py``. Included by config/urls.py (inside the A2 fenced block) WITHOUT an
``api/v1/`` prefix and WITHOUT a slug (TenantSlugMiddleware strips the org slug, ADR-001). So
request ``/<org_slug>/segments`` arrives here as ``segments``.

  GET  segments              → segments_page    (list of saved segments)
  GET  segments/new          → segment_builder  (new-segment builder)
  POST segments/new          → segment_save     (create via services.create_segment)
  POST segments/preview      → segment_preview  (HTMX live count + sample list)
  GET  segments/<id>         → segment_builder  (edit-segment builder)
  POST segments/<id>         → segment_save     (replace via services.replace_segment)

Path ordering: the static suffixes (``segments/new``, ``segments/preview``) MUST precede the
``segments/<uuid:id>`` dynamic route so Django matches them first (else ``new``/``preview`` would
be parsed as a UUID and 404).
"""
from django.urls import path

from . import views_htmx

app_name = "segments_htmx"

urlpatterns = [
    path("segments", views_htmx.segments_page, name="segments"),
    # Static suffixes BEFORE the <uuid> detail route.
    path("segments/new", views_htmx.segment_builder, name="segment-new"),
    path("segments/preview", views_htmx.segment_preview, name="segment-preview"),
    path("segments/<uuid:segment_id>", views_htmx.segment_builder, name="segment-edit"),
]
