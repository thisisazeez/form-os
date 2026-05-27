"""
Follow-up dispatch service — the REAL pipeline (trigger eval -> recipient -> render -> enqueue).

This is the home for follow-up business logic, called from the response-finalize hook
(apps.responses.public_services.finalize_response, parallel to the webhook dispatch). The
EMAIL *transport* is mocked-friendly (it reuses the existing eager-friendly Celery
send_mail_task + the configured EMAIL_BACKEND — console/dev, locmem/tests, Postmark/prod), but
EVERYTHING ELSE is real and tested:

  1. **Trigger evaluation** — for each follow-up on the survey, fire iff trigger.type ==
     'responseFinished' AND (no condition OR the condition evaluates true against the FINAL
     Response.data). The condition is a #/$defs/conditionGroup; we evaluate it with the SAME
     frozen Python evaluator as survey logic (apps.surveys.logic._eval_condition) so there is
     ZERO drift between logic gating and follow-up gating.

  2. **Recipient resolution** — server-derived (rbac.md §11):
       - {type:'contact'}                 -> the response's Contact's `email` attribute.
       - {type:'fixedEmail', email}       -> the static address.
       - {type:'responseField', questionId} -> the email the RESPONDENT typed (a contactInfo
         `email` sub-field, or an answer that is itself an email string).
     No resolvable email -> the delivery is recorded SKIPPED (not a failure), no send.

  3. **Render** — subject + body i18n-resolved to the response's language (fallback chain
     mirrors the TS resolveI18n), THEN recall tokens resolved against Response.data via the
     Python recall resolver (the recall.ts twin). Subject is HTML-stripped (subjects are plain
     text); body is sent as HTML.

  4. **Enqueue** — one async deliver_follow_up task per firing follow-up, given the active
     schema name (the worker re-activates it, like the webhook task). The task does the send +
     records SENT/FAILED on the FollowUpDelivery row.

IDEMPOTENT + SAFE: a unique(response, follow_up_id) row is created up-front; a re-fire finds
the existing row and skips. The whole dispatch is wrapped so it NEVER raises into finalize
(a follow-up problem must not break the respondent's submission).
"""
from __future__ import annotations

import logging
from typing import Any

from django.db import IntegrityError, connection, transaction

from apps.surveys.logic import _eval_condition

from .enums import (
    FollowUpActionType,
    FollowUpDeliveryStatus,
    FollowUpRecipientType,
    FollowUpTriggerType,
)
from .models import FollowUpDelivery

logger = logging.getLogger(__name__)

__all__ = [
    "dispatch_follow_ups",
    "follow_up_should_fire",
    "resolve_recipient",
    "render_subject",
    "render_body",
]

# Well-known contact attribute key carrying the email address (mirrors contacts.services).
_EMAIL_ATTR_KEY = "email"


# --------------------------------------------------------------------------- #
#  i18n resolution (the Python twin of frontend/src/lib/i18n.resolveI18n)      #
# --------------------------------------------------------------------------- #
def _default_language_code(languages: list[dict[str, Any]] | None) -> str:
    """The code of the survey's default-language entry (default=true), or 'default'."""
    for lang in languages or []:
        if isinstance(lang, dict) and lang.get("default"):
            return lang.get("code") or "default"
    return "default"


def _resolve_i18n(
    i18n: dict[str, Any] | None, active_lang: str, default_lang_code: str
) -> str:
    """Resolve an i18nString map for the active language (frozen fallback chain).

    active_lang -> default_lang_code -> the reserved 'default' key -> "". Mirrors resolveI18n.
    """
    if not isinstance(i18n, dict):
        return ""
    if active_lang and i18n.get(active_lang) is not None:
        return i18n[active_lang]
    if default_lang_code and i18n.get(default_lang_code) is not None:
        return i18n[default_lang_code]
    if i18n.get("default") is not None:
        return i18n["default"]
    return ""


# --------------------------------------------------------------------------- #
#  Trigger evaluation                                                          #
# --------------------------------------------------------------------------- #
def follow_up_should_fire(follow_up: dict[str, Any], answers: dict[str, Any]) -> bool:
    """True iff ``follow_up``'s trigger fires for a finished response with ``answers``.

    v1: fires when trigger.type == 'responseFinished' and either there is no condition or the
    condition (a #/$defs/conditionGroup) evaluates true against ``answers`` (Response.data +
    captured hidden fields). Uses the frozen survey-logic evaluator (no drift).
    """
    trigger = follow_up.get("trigger") or {}
    if trigger.get("type") != FollowUpTriggerType.RESPONSE_FINISHED:
        return False
    condition = trigger.get("condition")
    if not isinstance(condition, dict):
        return True  # unconditional -> always fire on finish
    try:
        return _eval_condition(condition, answers)
    except Exception:  # noqa: BLE001 — a malformed condition never breaks dispatch.
        logger.warning("follow-up condition eval failed; treating as no-fire", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
#  Recipient resolution (server-derived)                                       #
# --------------------------------------------------------------------------- #
def _contact_email(contact_id) -> str | None:
    """The `email` attribute value for ``contact_id`` in the active tenant schema, or None."""
    if contact_id is None:
        return None
    # Local import: contacts is a sibling tenant app; avoid a load-time cycle.
    from apps.contacts.models import ContactAttribute

    attr = (
        ContactAttribute.objects.filter(
            contact_id=contact_id, attribute_key__key=_EMAIL_ATTR_KEY
        )
        .values_list("value", flat=True)
        .first()
    )
    if isinstance(attr, str) and attr.strip():
        return attr.strip()
    return None


def _answer_email(answer: Any) -> str | None:
    """Extract an email string from a response answer (responseField recipient).

    Accepts a contactInfo answer (``{email: "...", ...}``) or an answer that is itself the
    email string (an openText/contactInfo email field). Returns None when no usable email.
    """
    if isinstance(answer, str) and answer.strip():
        return answer.strip()
    if isinstance(answer, dict):
        email = answer.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
    return None


def resolve_recipient(
    recipient: dict[str, Any], *, answers: dict[str, Any], contact_id
) -> str | None:
    """Resolve a #/$defs/followUpRecipient to an email address (or None when unresolvable).

    {type:'contact'}        -> the response's Contact email attribute (server-derived).
    {type:'fixedEmail'}     -> the static address.
    {type:'responseField'}  -> the email the respondent typed at the given question id.
    """
    rtype = (recipient or {}).get("type")
    if rtype == FollowUpRecipientType.FIXED_EMAIL:
        email = recipient.get("email")
        return email.strip() if isinstance(email, str) and email.strip() else None
    if rtype == FollowUpRecipientType.CONTACT:
        return _contact_email(contact_id)
    if rtype == FollowUpRecipientType.RESPONSE_FIELD:
        qid = recipient.get("questionId")
        if qid is None:
            return None
        return _answer_email((answers or {}).get(qid))
    return None


# --------------------------------------------------------------------------- #
#  Render (i18n -> recall -> plain/HTML)                                        #
# --------------------------------------------------------------------------- #
def _strip_html(text: str) -> str:
    """Collapse an HTML/recall-resolved subject to plain text (subjects are plain)."""
    import re

    return re.sub(r"<[^>]+>", "", text).strip()


def render_subject(
    action: dict[str, Any],
    *,
    answers: dict[str, Any],
    active_lang: str,
    default_lang_code: str,
) -> str:
    """i18n-resolve + recall-resolve the action's subject, then strip HTML (plain text)."""
    from .recall import resolve_recall

    raw = _resolve_i18n(action.get("subject"), active_lang, default_lang_code)
    return _strip_html(resolve_recall(raw, answers))


def render_body(
    action: dict[str, Any],
    *,
    answers: dict[str, Any],
    active_lang: str,
    default_lang_code: str,
) -> str:
    """i18n-resolve + recall-resolve the action's HTML body (sent as the HTML alternative)."""
    from .recall import resolve_recall

    raw = _resolve_i18n(action.get("body"), active_lang, default_lang_code)
    return resolve_recall(raw, answers)


# --------------------------------------------------------------------------- #
#  Dispatch (the finalize hook entry point)                                    #
# --------------------------------------------------------------------------- #
def dispatch_follow_ups(response) -> int:
    """Evaluate + enqueue every firing follow-up for a freshly finalized ``response``.

    Returns the number of follow-ups enqueued (0 if none fire / none defined). BEST-EFFORT:
    any error is swallowed (this runs as a finalize side effect and MUST NOT break the
    submission). Must be called with the tenant schema ACTIVE; the active schema name is
    captured and handed to the async task (worker pattern, like webhooks).

    For each firing follow-up a FollowUpDelivery row is created (idempotent via the
    unique(response, follow_up_id) constraint); if a row already exists (re-finalize) the
    follow-up is skipped. PENDING rows are then handed to ``deliver_follow_up.delay``.
    """
    try:
        survey = response.survey
        follow_ups = survey.follow_ups or []
        if not follow_ups:
            return 0
        answers = response.data or {}
        # VARIABLES (additive): enrich Response.data with the survey's live variable values so a
        # follow-up condition / recall token can read a variable (recall-of-variable). Variables
        # are NOT persisted into Response.data — they are recomputed deterministically here from
        # the final answers. No-variable surveys -> compute_variables returns {} -> answers is
        # unchanged (byte-identical to today).
        from apps.surveys.logic import compute_variables

        survey_def = {
            "questions": survey.questions or [],
            "variables": survey.variables or [],
        }
        _vars = compute_variables(survey_def, answers)
        if _vars:
            answers = {**answers, **_vars}
        active_lang = response.language or "default"
        default_lang_code = _default_language_code(survey.languages)
        schema_name = connection.schema_name

        from .tasks import deliver_follow_up

        count = 0
        for follow_up in follow_ups:
            if not isinstance(follow_up, dict):
                continue
            action = follow_up.get("action") or {}
            if action.get("type") != FollowUpActionType.SEND_EMAIL:
                continue
            if not follow_up_should_fire(follow_up, answers):
                continue

            follow_up_id = follow_up.get("id")
            if not follow_up_id:
                continue

            recipient_email = resolve_recipient(
                action.get("recipient") or {},
                answers=answers,
                contact_id=response.contact_id,
            )
            subject = render_subject(
                action,
                answers=answers,
                active_lang=active_lang,
                default_lang_code=default_lang_code,
            )

            # Create the idempotent delivery row. A duplicate (re-finalize) -> skip this one.
            try:
                with transaction.atomic():
                    if recipient_email:
                        delivery = FollowUpDelivery.objects.create(
                            survey=survey,
                            response=response,
                            follow_up_id=follow_up_id,
                            recipient_email=recipient_email,
                            rendered_subject=subject[:998],
                            status=FollowUpDeliveryStatus.PENDING,
                        )
                    else:
                        # Fired but no recipient -> SKIPPED (terminal; no send enqueued).
                        FollowUpDelivery.objects.create(
                            survey=survey,
                            response=response,
                            follow_up_id=follow_up_id,
                            recipient_email="",
                            rendered_subject=subject[:998],
                            status=FollowUpDeliveryStatus.SKIPPED,
                        )
                        continue
            except IntegrityError:
                # Already dispatched for this (response, follow-up) — idempotent no-op.
                continue

            deliver_follow_up.delay(
                schema_name=schema_name,
                delivery_id=str(delivery.id),
                follow_up=follow_up,
                answers=answers,
                active_lang=active_lang,
                default_lang_code=default_lang_code,
            )
            count += 1
        return count
    except Exception:  # noqa: BLE001 — never break finalize on a follow-up problem.
        logger.exception("follow-up dispatch failed (swallowed; finalize unaffected)")
        return 0
