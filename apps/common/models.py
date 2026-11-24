"""
Base model mixins — the global conventions from contracts/models.md §0.

Every onForm model (public AND tenant) uses:
  - UUIDv4 primary key (ADR-003): non-enumerable in public URLs, no cross-schema PK
    collisions, safe to expose in the API and to copy into `*_user_id` columns.
  - created_at / updated_at timestamps (audit/sort baseline).

`apps.common` itself defines NO concrete models (it's a shared lib — models.md PART B);
these are abstract bases that the public + tenant apps inherit. Wave 2 apps subclass
`UUIDModel` (or `TimeStampedUUIDModel`) for every model.
"""
import uuid

from django.db import models


class UUIDPrimaryKeyMixin(models.Model):
    """UUIDv4 PK (ADR-003). `editable=False` keeps it out of forms/serializers as input."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    class Meta:
        abstract = True


class TimeStampedMixin(models.Model):
    """created_at / updated_at on all non-trivial models (models.md §0)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TimeStampedUUIDModel(UUIDPrimaryKeyMixin, TimeStampedMixin):
    """Convenience base = UUID PK + timestamps. The default base for Wave 2 models."""

    class Meta:
        abstract = True


# Back-compat alias used in some docs/snippets.
UUIDModel = TimeStampedUUIDModel
