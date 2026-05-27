"""
Contacts HTMX (server-rendered admin) URLs — the browser pages, distinct from the DRF API in
``apps/contacts/urls.py``. Included by config/urls.py (inside the A2 fenced block) WITHOUT an
``api/v1/`` prefix and WITHOUT a slug (TenantSlugMiddleware strips the org slug before the
urlconf matches, ADR-001). So request ``/<org_slug>/contacts`` arrives here as ``contacts``.

  GET  contacts                       → contacts_page          (list + search + filters + rail)
  POST contacts/new                   → contact_create         (New-contact modal target)
  GET  contacts/import                → contact_import          (CSV upload page)
  POST contacts/import                → contact_import          (CSV upload → per-row results)
  GET  contacts/export.csv            → contact_export          (streamed RBAC/PII-aware CSV)
  POST contacts/save-as-segment       → contacts_save_as_segment (bulk "Add to segment" pivot)
  POST contacts/<id>/delete           → contact_delete          (bulk-bar / profile delete)
  POST contacts/<id>/attributes       → contact_add_attribute   (profile "Add attribute")
  GET  contacts/<id>                  → contact_profile_page    (identity card + activity)

Path ordering: every STATIC suffix (``new`` / ``import`` / ``export.csv`` /
``save-as-segment`` / ``<id>/delete`` / ``<id>/attributes``) is listed before the bare
``contacts/<uuid:id>`` detail so Django matches the specific routes first.
"""
from django.urls import path

from . import views_htmx

app_name = "contacts_htmx"

urlpatterns = [
    path("contacts", views_htmx.contacts_page, name="contacts"),
    path("contacts/new", views_htmx.contact_create, name="contact-create"),
    path("contacts/import", views_htmx.contact_import, name="contact-import"),
    path("contacts/export.csv", views_htmx.contact_export, name="contact-export"),
    path(
        "contacts/save-as-segment",
        views_htmx.contacts_save_as_segment,
        name="contacts-save-as-segment",
    ),
    path(
        "contacts/<uuid:contact_id>/delete",
        views_htmx.contact_delete,
        name="contact-delete",
    ),
    path(
        "contacts/<uuid:contact_id>/attributes",
        views_htmx.contact_add_attribute,
        name="contact-add-attribute",
    ),
    path(
        "contacts/<uuid:contact_id>",
        views_htmx.contact_profile_page,
        name="contact-profile",
    ),
]
