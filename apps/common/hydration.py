"""
Cross-schema user resolution — the `hydrate_users()` contract (ADR-002 / models.md PART A).

THE #1 FOOTGUN, decided ONCE here. django-tenants forbids foreign keys across Postgres
schemas. `User` lives in the PUBLIC schema; tenant models live in per-org schemas. So any
tenant model that references a user stores the public `User`'s UUID PK as a plain
`UUIDField` column (`user_id` / `<role>_user_id`) — NEVER a ForeignKey. This module is the
ONLY sanctioned way to turn those columns back into `User` objects.

Frozen contract (models.md PART A):
  - One public-schema query regardless of input size (N+1 is forbidden; reviewers reject
    per-row `User.objects.get(...)` in tenant code — REVIEW.md §3.2).
  - Resolved users are attached as Python attributes named `<field-minus-"_id">{attr_suffix}`,
    e.g. user_id -> user_obj, actor_user_id -> actor_user_obj, created_by_user_id -> created_by_user_obj.
  - Serializers MUST read the hydrated `*_obj` attribute, never query User per row.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from django.contrib.auth import get_user_model
from django.db import models
from django_tenants.utils import schema_context

__all__ = ["hydrate_users", "UserHydrationQuerySetMixin", "UserHydrationManager"]


def _attr_name(field: str, attr_suffix: str) -> str:
    """user_id -> user{suffix};  actor_user_id -> actor_user{suffix}.

    Strips a trailing '_id' if present; otherwise appends the suffix to the whole name.
    """
    base = field[:-3] if field.endswith("_id") else field
    return f"{base}{attr_suffix}"


def hydrate_users(
    objects: Iterable[models.Model],
    *,
    fields: Sequence[str] = ("user_id",),
    attr_suffix: str = "_obj",
) -> list[models.Model]:
    """Batch-resolve public `User`s referenced by tenant `*_user_id` UUID columns.

    Behavior (frozen — models.md PART A):
      1. Collect the set of non-null UUIDs across all `fields` over all `objects`.
      2. In ONE query against the PUBLIC schema, fetch User.objects.filter(id__in=ids)
         and build an {id: User} map. (Schema switch handled here via schema_context;
         callers never think about it.)
      3. Attach the resolved User (or None) onto each object as `<field-minus-_id>{suffix}`.
      4. Return the same list (mutated in place) for chaining.
      5. N+1 forbidden: at most ONE public-schema query regardless of input size.

    Args:
        objects: any iterable of model instances carrying the given UUID column(s).
        fields: the UUID column name(s) to resolve. Default ("user_id",).
        attr_suffix: suffix for the attached attribute. Default "_obj".

    Returns:
        A list of the same objects, each with `*_obj` attributes set (User or None).
    """
    User = get_user_model()
    materialized = list(objects)

    # 1. Collect distinct non-null UUIDs across all requested fields.
    ids: set = set()
    for obj in materialized:
        for field in fields:
            value = getattr(obj, field, None)
            if value is not None:
                ids.add(value)

    # 2. ONE query against the public schema → {id: User}.
    user_map: dict = {}
    if ids:
        with schema_context("public"):
            for user in User.objects.filter(id__in=ids):
                user_map[user.pk] = user

    # 3. Attach `<field>_obj` (User or None) to each object.
    for obj in materialized:
        for field in fields:
            value = getattr(obj, field, None)
            resolved = user_map.get(value) if value is not None else None
            setattr(obj, _attr_name(field, attr_suffix), resolved)

    # 4. Return the (mutated) list for chaining.
    return materialized


class UserHydrationQuerySetMixin:
    """QuerySet mixin exposing `.hydrate_users(*fields)` per the frozen helper.

    Evaluates the queryset and returns a LIST with `*_obj` attributes attached, so call
    sites read (models.md PART A example):

        members = TeamMembership.objects.filter(team=team).hydrate_users("user_id")
        # members[i].user_obj is a public User (or None)

    Returning a list (not a lazy QuerySet) is intentional: hydration is a terminal,
    eager operation that performs exactly one extra public-schema query.
    """

    def hydrate_users(self, *fields: str, attr_suffix: str = "_obj") -> list[models.Model]:
        resolve_fields = fields or ("user_id",)
        return hydrate_users(list(self), fields=resolve_fields, attr_suffix=attr_suffix)


class UserHydrationManager(models.Manager):
    """Manager whose QuerySet carries the hydration mixin. Wave 2 tenant models that hold
    `*_user_id` columns set `objects = UserHydrationManager()` to get `.hydrate_users(...)`
    on their default queryset."""

    def get_queryset(self) -> models.QuerySet:
        qs_class = type(
            "UserHydrationQuerySet",
            (UserHydrationQuerySetMixin, models.QuerySet),
            {},
        )
        return qs_class(self.model, using=self._db)
