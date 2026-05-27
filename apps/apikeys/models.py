"""
apikeys — TENANT schema (TENANT_APPS). ApiKey (contracts/models.md §2.15). Tenant/environment-scoped API auth.

TENANT-schema model. FK(Environment) is tenant→tenant (OK). `created_by_user_id` is the
cross-schema User pattern (ADR-002).

Only the HASH of the key is stored (hashed_key, REVIEW.md §3.6); the raw key is shown ONCE
at creation. The DRF authenticator (Wave 2c) hashes the presented `Authorization: ApiKey
<raw>` and looks up hashed_key; the matched key pins the request's tenant + environment.

PLACEMENT NOTE (honored from the scaffold): models.md PART B groups ApiKey + Webhook under
an `integrations` app; the scaffold kept `apikeys` separate (Phase-0 brief). Consolidating
is a coordinated re-broadcast (PLAN §15), out of scope for the model owner.
"""
from django.db import models

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel
from apps.workspaces.models import Environment


class ApiKey(TimeStampedUUIDModel):
    """Tenant/environment-scoped API key (models.md §2.15). Key scopes to one environment."""

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    label = models.CharField(max_length=255)
    # ONLY the hash is stored (e.g. SHA-256 of the raw key). Raw key shown once at creation.
    hashed_key = models.CharField(max_length=128, unique=True)
    # First chars of the raw key (e.g. "osk_live_") for identification in lists; non-secret.
    prefix = models.CharField(max_length=12)
    # Optional capability scopes (e.g. ["surveys:read","responses:read"]). Reserved in v1.
    # Callable default (mutable) per Django rules.
    scopes = models.JSONField(default=list)
    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Soft-revoke; revoked keys reject (auth checks revoked_at).
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = UserHydrationManager()

    class Meta:
        db_table = "apikeys_apikey"
        indexes = [
            models.Index(fields=["environment"], name="ix_apikey_environment"),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.prefix}…)"
