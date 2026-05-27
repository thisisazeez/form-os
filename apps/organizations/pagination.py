"""
Pagination shaped to contracts/openapi.yaml ``#/components/schemas/Paginated``.

DRF's default ``PageNumberPagination.get_paginated_response`` emits
``{count, next, previous, results}``, but the frozen contract is
``{count, page, page_size, results}``. This subclass emits the contract shape and honors the
openapi ``page`` / ``page_size`` query params (page_size capped at 200, the openapi maximum).
"""
from __future__ import annotations

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class OpenapiPageNumberPagination(PageNumberPagination):
    page_size_query_param = "page_size"  # openapi PageSize query param
    max_page_size = 200  # openapi PageSize maximum

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "results": data,
            }
        )
