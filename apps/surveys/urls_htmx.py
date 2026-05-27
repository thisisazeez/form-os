"""
Tenant HTMX (server-rendered admin) URLs for the Survey surface — included by config/urls.py
(slug already stripped by TenantSlugMiddleware, so e.g. ``/<org_slug>/surveys`` arrives here as
``surveys``). Distinct from ``apps/surveys/urls.py`` (the ``api/v1/...`` DRF Survey CRUD).

Namespace: ``surveys_htmx``.
  ``surveys``                      surveys_list   (GET list; ?new=1 → gallery; POST blank DRAFT → builder)
  ``surveys/new``                  survey_new     (GET template gallery; POST create-from-template → builder)
  ``surveys/<uuid:survey_id>/share``        survey_share     (link, embed, QR, email, personal links)
  ``surveys/<uuid:survey_id>/share/links``  survey_mint_links (POST: mint per-contact SurveyLinks)
  ``surveys/<uuid:survey_id>/share/links.csv`` survey_links_csv (download name,email,personal_url)
  ``surveys/<uuid:survey_id>/share/qr``     survey_qr        (QR PNG/SVG of the public link)
  ``surveys/<uuid:survey_id>/results``      survey_results

The dashboard home (``/<org_slug>/`` → ``dashboard_home``) and the survey-builder host
(``surveys/<id>/edit``) are wired directly in config/urls.py (the builder host predates this
module). The Responses table/detail pages are wired via ``apps/responses/urls_htmx.py``.
"""
from django.urls import path

from . import views_htmx

app_name = "surveys_htmx"

urlpatterns = [
    path("surveys", views_htmx.surveys_list, name="list"),
    # The "Create a new survey" template gallery (WS-14). MUST precede the
    # ``surveys/<uuid:survey_id>/...`` routes so the literal "new" segment isn't shadowed (it
    # isn't a UUID, so the uuid converter would reject it anyway — but order it first for clarity).
    path("surveys/new", views_htmx.survey_new, name="new"),
    # The sidebar "Results" landing — an index of surveys, each linking to its results page.
    path("results", views_htmx.survey_results_index, name="results-index"),
    path("surveys/<uuid:survey_id>/share", views_htmx.survey_share, name="share"),
    path(
        "surveys/<uuid:survey_id>/share/links",
        views_htmx.survey_mint_links,
        name="share-mint-links",
    ),
    path(
        "surveys/<uuid:survey_id>/share/links.csv",
        views_htmx.survey_links_csv,
        name="share-links-csv",
    ),
    path("surveys/<uuid:survey_id>/share/qr", views_htmx.survey_qr, name="share-qr"),
    path("surveys/<uuid:survey_id>/results", views_htmx.survey_results, name="results"),
]
