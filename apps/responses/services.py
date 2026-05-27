"""
ADMIN-side service layer for the responses app (WS-7 Share page).

This is the AUTHENTICATED counterpart to ``public_services`` (the unauthenticated runtime).
Its single concern is **minting personalized ``SurveyLink`` rows** for a survey from the
Share page so an operator can hand each contact a unique, trackable link.

Why a NEW module (not ``public_services``):
  ``public_services`` is documented as the PUBLIC runtime path — it *resolves and consumes*
  tokens. Minting is an admin action (RBAC-gated upstream in the HTMX view) and must not live
  on the public surface. The two share the ``SurveyLink`` model and the same token column, so
  a minted token resolves through the EXISTING ``public_services.resolve_link`` path with no
  new code (the load-bearing integration: ``/<org>/s/<id>?link_token=<token>``).

Token shape: ``secrets.token_urlsafe(32)`` → 43 URL-safe chars, well within the model's
``max_length=64`` and matching the entropy ``public_services`` already trusts.

Idempotency: ``mint_links_for_contacts`` SKIPS any contact that already has an UNCONSUMED,
unexpired link for the survey, so re-clicking "Generate" does not flood the table with
duplicate live links. A contact whose only link is consumed/expired DOES get a fresh one.
"""
from __future__ import annotations

import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.surveys.models import Survey

from .models import SurveyLink

# Matches the entropy public_services trusts; 43 url-safe chars fit max_length=64.
_TOKEN_NBYTES = 32
# Bound the re-mint retry loop on the (astronomically rare) unique-token collision.
_MAX_TOKEN_ATTEMPTS = 5


def _new_token() -> str:
    """A fresh URL-safe token for a SurveyLink (``secrets`` → CSPRNG)."""
    return secrets.token_urlsafe(_TOKEN_NBYTES)


def _has_live_link(survey: Survey, contact_id) -> bool:
    """True iff ``contact_id`` already holds an unconsumed, unexpired link for ``survey``.

    "Live" = not consumed AND (no expiry OR expiry in the future). A consumed/expired link
    does NOT block a fresh mint (the contact needs a usable link again).
    """
    now = timezone.now()
    qs = SurveyLink.objects.filter(
        survey=survey, contact_id=contact_id, consumed_at__isnull=True
    )
    return qs.filter(expires_at__isnull=True).exists() or qs.filter(
        expires_at__gt=now
    ).exists()


def _create_link(survey: Survey, contact_id, *, expires_at=None) -> SurveyLink:
    """Insert one SurveyLink, re-minting the token on the rare unique collision."""
    last_exc: IntegrityError | None = None
    for _ in range(_MAX_TOKEN_ATTEMPTS):
        try:
            with transaction.atomic():
                return SurveyLink.objects.create(
                    survey=survey,
                    contact_id=contact_id,
                    token=_new_token(),
                    expires_at=expires_at,
                )
        except IntegrityError as exc:  # token collision (unique=True) → re-mint
            last_exc = exc
            continue
    # Exhausted attempts (effectively impossible with 256 bits of entropy).
    raise last_exc if last_exc is not None else RuntimeError("Could not mint a link token.")


def mint_links_for_contacts(
    survey: Survey, contact_ids, *, expires_at=None
) -> list[SurveyLink]:
    """Mint one personalized ``SurveyLink`` per contact id that lacks a LIVE link.

    Idempotent: a contact with an existing unconsumed/unexpired link for this survey is
    SKIPPED (no duplicate live link). Returns the NEWLY-created links (possibly empty).
    Contact association is the link's sole trusted source (``public_services`` reads
    ``SurveyLink.contact_id`` server-side; the client never supplies it). Must be called
    with the tenant schema ACTIVE.
    """
    created: list[SurveyLink] = []
    for contact_id in contact_ids:
        if contact_id is None or _has_live_link(survey, contact_id):
            continue
        created.append(_create_link(survey, contact_id, expires_at=expires_at))
    return created


def links_for_survey(survey: Survey):
    """All ``SurveyLink`` rows for ``survey``, newest first (admin listing/export).

    Returns a queryset (caller paginates/limits). Must be called with the tenant schema ACTIVE.
    """
    return SurveyLink.objects.filter(survey=survey).order_by("-created_at", "id")


def personal_url(public_base_url: str, link: SurveyLink) -> str:
    """Append ``?link_token=<token>`` to the survey's public URL for a personalized link.

    ``public_base_url`` is the absolute open-runtime URL (``/<org>/s/<id>``) the Share view
    already builds. The token rides through to ``public_runtime_host`` → the runtime island →
    the public JSON endpoints, where ``public_services.resolve_link`` validates it. We never
    re-implement token resolution here.
    """
    sep = "&" if "?" in public_base_url else "?"
    return f"{public_base_url}{sep}link_token={link.token}"
