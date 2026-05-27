"""
DRF API views for Contacts + ContactAttributeKeys (contracts/openapi.yaml — Contacts tag).

TENANT-prefixed (mounted at ``api/v1/...`` after TenantSlugMiddleware strips the org slug —
config/urls.py). The routed org is ``request.tenant``; ``IsOrgMember`` gates membership and
caches ``request.org_membership`` / ``request.organization``.

Authorization delegates to ``apps.common.permissions.can()`` (rbac.md §7) — the single entry
point. ``permission_action_map`` on each view declares the action→permission mapping
(auditable, rbac.md §8.1). Contacts/keys are workspace-scoped via ``.environment.workspace``
(workspace_of() in permissions.py), so views load with ``select_related("environment__
workspace")`` and pass the instance to ``can()`` (no extra query inside the object check).

LIST endpoints FILTER, not 403 (rbac.md §8.1): list contacts/keys returns only those in
environments whose workspace the caller can ``read``, computed via the precomputed
``workspace_access_map(user)`` (the workspaces helper) — no per-row ``can()``. Detail GETs for
an object the caller cannot see return 404 (no existence oracle — matches WorkspaceDetailView).

PII REDACTION: a caller with workspace access >= ``write`` sees raw PII attribute values; a
``read``-only caller sees them masked (serializers.PII_MASK). The view computes the privilege
once (from the access map / effective_access) and passes ``pii_unmasked`` to the serializer.

openapi (Contacts tag — the spec uses summaries; mapped here by path+method). Endpoints marked
[ADD] are additions the brief requires that are NOT in openapi.yaml (attribute-key management),
flagged in the producer summary:
  GET    /environments/{env_id}/contacts            → ContactListCreateView.get   (view_contact, filtered)
  POST   /environments/{env_id}/contacts            → ContactListCreateView.post  (create_contact; upsert-by-email)
  GET    /contacts/{id}                              → ContactDetailView.get       (view_contact)
  PATCH  /contacts/{id}                              → ContactDetailView.patch     (edit_contact)
  DELETE /contacts/{id}                              → ContactDetailView.delete    (delete_contact; anonymizes responses)
  GET    /environments/{env_id}/attribute-keys [ADD] → AttributeKeyListCreateView.get  (view_contact, filtered)
  POST   /environments/{env_id}/attribute-keys [ADD] → AttributeKeyListCreateView.post (create_contact)
  GET    /attribute-keys/{id} [ADD]                  → AttributeKeyDetailView.get   (view_contact)
  PATCH  /attribute-keys/{id} [ADD]                  → AttributeKeyDetailView.patch (edit_contact)
  DELETE /attribute-keys/{id} [ADD]                  → AttributeKeyDetailView.delete (edit_contact)
"""
from __future__ import annotations

from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsOrgMember, can
from apps.contacts import services
from apps.contacts.models import Contact, ContactAttributeKey
from apps.contacts.scoping import (
    get_environment_or_404,
    pii_unmasked_for_environment,
)
from apps.contacts.serializers import (
    ContactAttributeKeyCreateSerializer,
    ContactAttributeKeySerializer,
    ContactAttributeKeyUpdateSerializer,
    ContactSerializer,
    ContactWriteSerializer,
)


class ContactsPagination(PageNumberPagination):
    """openapi Paginated shape: {count, page, page_size, results}. ``?page_size=`` overridable."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "results": data,
            }
        )


def _contact_detail_qs():
    """Contact queryset with the workspace chain + attributes prefetched (one object check
    query path; no N+1 in the serializer)."""
    return Contact.objects.select_related("environment__workspace").prefetch_related(
        "attributes__attribute_key"
    )


def _get_contact_or_404(contact_id) -> Contact:
    try:
        return _contact_detail_qs().get(pk=contact_id)
    except (Contact.DoesNotExist, ValueError):
        raise Http404("Contact not found.") from None


class ContactListCreateView(APIView):
    """GET (list, filtered, paginated) / POST (create-or-upsert) contacts in an environment."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_contact", "post": "create_contact"}

    def get(self, request, environment_id):
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "view_contact", environment):
            raise Http404("Environment not found.")
        unmasked = pii_unmasked_for_environment(request.user, environment)
        qs = (
            Contact.objects.filter(environment=environment)
            .prefetch_related("attributes__attribute_key")
            .order_by("created_at", "id")
        )
        paginator = ContactsPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = ContactSerializer(
            page, many=True, context={"request": request, "pii_unmasked": unmasked}
        ).data
        return paginator.get_paginated_response(data)

    def post(self, request, environment_id):
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "create_contact", environment):
            return Response(
                {"detail": "You do not have permission to create contacts here."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ContactWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            contact, created = services.upsert_contact(
                environment=environment,
                external_id=serializer.validated_data.get("external_id"),
                attributes=serializer.validated_data.get("attributes", []),
                created_by_user_id=request.user.id,
            )
        except services.AttributeValueError as exc:
            return Response(
                {"detail": "Invalid attribute value(s).", "errors": {"attributes": exc.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(
                {"detail": "A contact with this external_id already exists."},
                status=status.HTTP_409_CONFLICT,
            )
        # Creator can always see raw PII (they're at least write on this env to create).
        contact = _contact_detail_qs().get(pk=contact.id)
        body = ContactSerializer(
            contact, context={"request": request, "pii_unmasked": True}
        ).data
        return Response(
            body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class ContactDetailView(APIView):
    """GET (read) / PATCH (edit attributes) / DELETE (GDPR erasure) a contact."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {
        "get": "view_contact",
        "patch": "edit_contact",
        "delete": "delete_contact",
    }

    def get(self, request, contact_id):
        contact = _get_contact_or_404(contact_id)
        if not can(request.user, "view_contact", contact):
            raise Http404("Contact not found.")  # no existence oracle.
        unmasked = pii_unmasked_for_environment(request.user, contact.environment)
        return Response(
            ContactSerializer(
                contact, context={"request": request, "pii_unmasked": unmasked}
            ).data
        )

    def patch(self, request, contact_id):
        contact = _get_contact_or_404(contact_id)
        if not can(request.user, "edit_contact", contact):
            return Response(
                {"detail": "You do not have permission to edit this contact."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ContactWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        external_id = (
            serializer.validated_data["external_id"]
            if "external_id" in serializer.validated_data
            else "__unset__"
        )
        try:
            services.update_contact_attributes(
                contact=contact,
                external_id=external_id,
                attributes=serializer.validated_data.get("attributes", []),
            )
        except services.AttributeValueError as exc:
            return Response(
                {"detail": "Invalid attribute value(s).", "errors": {"attributes": exc.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(
                {"detail": "A contact with this external_id already exists."},
                status=status.HTTP_409_CONFLICT,
            )
        contact = _contact_detail_qs().get(pk=contact.id)
        return Response(
            ContactSerializer(
                contact, context={"request": request, "pii_unmasked": True}
            ).data
        )

    def delete(self, request, contact_id):
        contact = _get_contact_or_404(contact_id)
        if not can(request.user, "delete_contact", contact):
            return Response(
                {"detail": "You do not have permission to delete this contact."},
                status=status.HTTP_403_FORBIDDEN,
            )
        services.delete_contact(contact=contact)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Attribute-key management ([ADD] — not in openapi; brief requires it).
# ---------------------------------------------------------------------------
def _get_attribute_key_or_404(key_id) -> ContactAttributeKey:
    try:
        return ContactAttributeKey.objects.select_related("environment__workspace").get(
            pk=key_id
        )
    except (ContactAttributeKey.DoesNotExist, ValueError):
        raise Http404("Attribute key not found.") from None


class AttributeKeyListCreateView(APIView):
    """GET (list) / POST (create) attribute keys for an environment. View = view_contact;
    create = create_contact (same workspace levels as contacts — they're contact metadata)."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {"get": "view_contact", "post": "create_contact"}

    def get(self, request, environment_id):
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "view_contact", environment):
            raise Http404("Environment not found.")
        keys = ContactAttributeKey.objects.filter(environment=environment).order_by("key")
        return Response(ContactAttributeKeySerializer(keys, many=True).data)

    def post(self, request, environment_id):
        environment = get_environment_or_404(environment_id)
        if not can(request.user, "create_contact", environment):
            return Response(
                {"detail": "You do not have permission to create attribute keys here."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ContactAttributeKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            key = services.create_attribute_key(
                environment=environment,
                key=serializer.validated_data["key"],
                data_type=serializer.validated_data["data_type"],
                label=serializer.validated_data.get("label", ""),
                is_pii=serializer.validated_data.get("is_pii", False),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError:
            return Response(
                {"detail": "An attribute key with this name already exists in this environment."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            ContactAttributeKeySerializer(key).data, status=status.HTTP_201_CREATED
        )


class AttributeKeyDetailView(APIView):
    """GET (read) / PATCH (edit label/is_pii/data_type) / DELETE an attribute key."""

    permission_classes = [IsAuthenticated, IsOrgMember]
    permission_action_map = {
        "get": "view_contact",
        "patch": "edit_contact",
        "delete": "edit_contact",
    }

    def get(self, request, key_id):
        key = _get_attribute_key_or_404(key_id)
        if not can(request.user, "view_contact", key):
            raise Http404("Attribute key not found.")
        return Response(ContactAttributeKeySerializer(key).data)

    def patch(self, request, key_id):
        key = _get_attribute_key_or_404(key_id)
        if not can(request.user, "edit_contact", key):
            return Response(
                {"detail": "You do not have permission to edit this attribute key."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ContactAttributeKeyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.update_attribute_key(
                attribute_key=key,
                label=serializer.validated_data.get("label"),
                is_pii=serializer.validated_data.get("is_pii"),
                data_type=serializer.validated_data.get("data_type"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ContactAttributeKeySerializer(key).data)

    def delete(self, request, key_id):
        key = _get_attribute_key_or_404(key_id)
        if not can(request.user, "edit_contact", key):
            return Response(
                {"detail": "You do not have permission to delete this attribute key."},
                status=status.HTTP_403_FORBIDDEN,
            )
        key.delete()  # cascades ContactAttribute rows for this key.
        return Response(status=status.HTTP_204_NO_CONTENT)
