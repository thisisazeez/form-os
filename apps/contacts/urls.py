"""
Contacts + ContactAttributeKey URLs (contracts/openapi.yaml — Contacts tag).

TENANT-prefixed — included by config/urls.py under ``api/v1/`` AFTER TenantSlugMiddleware
strips the org slug. So ``/api/v1/<slug>/environments/<env>/contacts`` arrives here as
``api/v1/environments/<env>/contacts`` (mirrors openapi ``/api/v1/{org_slug}/...``):

  GET/POST     api/v1/environments/<uuid:environment_id>/contacts        → ContactListCreateView
  GET/PATCH/DELETE api/v1/contacts/<uuid:contact_id>                     → ContactDetailView

Attribute-key management ([ADD] — required by the producer brief; not in openapi.yaml):
  GET/POST     api/v1/environments/<uuid:environment_id>/attribute-keys  → AttributeKeyListCreateView
  GET/PATCH/DELETE api/v1/attribute-keys/<uuid:key_id>                   → AttributeKeyDetailView
"""
from django.urls import path

from . import views

app_name = "contacts"

urlpatterns = [
    path(
        "environments/<uuid:environment_id>/contacts",
        views.ContactListCreateView.as_view(),
        name="contact-list",
    ),
    path(
        "contacts/<uuid:contact_id>",
        views.ContactDetailView.as_view(),
        name="contact-detail",
    ),
    # [ADD] attribute-key management.
    path(
        "environments/<uuid:environment_id>/attribute-keys",
        views.AttributeKeyListCreateView.as_view(),
        name="attribute-key-list",
    ),
    path(
        "attribute-keys/<uuid:key_id>",
        views.AttributeKeyDetailView.as_view(),
        name="attribute-key-detail",
    ),
]
