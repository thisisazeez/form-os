"""
Tenant HTMX (server-rendered admin) URLs for the Response surface — included by config/urls.py
(slug already stripped by TenantSlugMiddleware, so ``/<org_slug>/surveys/<id>/responses``
arrives here as ``surveys/<id>/responses``). Distinct from ``apps/responses/urls.py`` (the
``api/v1/...`` DRF Response READ API) and ``apps/responses/public_urls.py`` (public runtime).

Namespace: ``responses_htmx``.
  ``surveys/<uuid:survey_id>/responses``                            survey_responses (table + filters + tags)
  ``surveys/<uuid:survey_id>/responses.csv``                        response_export  (CSV, filtered, PII-masked)
  ``surveys/<uuid:survey_id>/responses.json``                       response_export  (JSON, filtered, PII-masked)
  ``surveys/<uuid:survey_id>/responses/<uuid:response_id>``         response_detail  (single Q&A + tags)
  ``surveys/<uuid:survey_id>/responses/<uuid:response_id>/tags``    response_tag_apply  (POST add tag)
  ``surveys/<uuid:survey_id>/responses/<rid>/tags/<tid>/remove``    response_tag_remove (POST remove tag)
  ``tags``                                                          tags_manage      (rename/delete/merge — admin)
  ``tags/<uuid:tag_id>/rename|delete|merge``                        tag mgmt actions (POST)

ORDERING: the ``responses.csv`` / ``responses.json`` export routes are declared BEFORE the
``responses/<uuid:response_id>`` detail route. They cannot collide (a dot vs a slash), but
keeping the more-specific literal-suffix routes first documents intent.
"""
from django.urls import path

from . import views_htmx

app_name = "responses_htmx"

urlpatterns = [
    # --- Export (literal .csv / .json suffix — before the detail catch-all) ---
    path(
        "surveys/<uuid:survey_id>/responses.csv",
        views_htmx.response_export,
        name="export_csv",
    ),
    path(
        "surveys/<uuid:survey_id>/responses.json",
        views_htmx.response_export,
        name="export_json",
    ),
    # --- Table + detail ---
    path(
        "surveys/<uuid:survey_id>/responses",
        views_htmx.survey_responses,
        name="list",
    ),
    path(
        "surveys/<uuid:survey_id>/responses/<uuid:response_id>",
        views_htmx.response_detail,
        name="detail",
    ),
    # --- Per-response tag apply / remove (POST) ---
    path(
        "surveys/<uuid:survey_id>/responses/<uuid:response_id>/tags",
        views_htmx.response_tag_apply,
        name="tag_apply",
    ),
    path(
        "surveys/<uuid:survey_id>/responses/<uuid:response_id>/tags/<uuid:tag_id>/remove",
        views_htmx.response_tag_remove,
        name="tag_remove",
    ),
    # --- Tag management (tenant-global; owner/admin) ---
    path("tags", views_htmx.tags_manage, name="tags_manage"),
    path("tags/<uuid:tag_id>/rename", views_htmx.tag_rename, name="tag_rename"),
    path("tags/<uuid:tag_id>/delete", views_htmx.tag_delete, name="tag_delete"),
    path("tags/<uuid:tag_id>/merge", views_htmx.tag_merge, name="tag_merge"),
]
