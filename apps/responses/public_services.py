"""
Public-runtime service layer for the take-a-survey loop (Wave 3a).

This is the business-logic home for the PUBLIC, unauthenticated survey path
(contracts/openapi.yaml "Public Survey" tag). The DRF public views call these so no
surface can bypass the invariants. Three concerns live here:

1. **Per-response ``edit_token``** (ADR-009): a short-lived token authorizing
   PATCH/finalize for ONE response. The task brief says "no model change expected",
   so the token is NOT a DB column — it is a ``django.core.signing.TimestampSigner``
   value that signs the response id with a namespaced salt and is verified with a
   ``max_age`` (``EDIT_TOKEN_MAX_AGE_SECONDS``). This gives single-response scope +
   expiry (REVIEW.md §3.5) with zero schema change. ``edit_token_expiry()`` returns
   the matching ``expires_at`` for the ``PublicResponseHandle``.

2. **``link_token`` → SurveyLink resolution** (ADR-009, §2.7a): the survey-level link
   token is the SOLE trusted source of contact association. We resolve
   ``SurveyLink.contact_id`` server-side and NEVER trust a client-supplied contact_id
   (the reconciled D-fix; rbac.md §11 / PublicResponseCreate description). A
   consumed/expired single-use link → ``LinkGoneError`` (→ 410). A single-use survey
   with NO link_token → ``LinkRequiredError`` (→ 410: "not accessible without a link").

3. **Atomic finalize + single-use consumption**: ``finalize_response`` marks the
   response ``finished`` AND, in the SAME transaction, consumes the response's
   SurveyLink (sets ``consumed_at``) for single-use surveys (models.md §2.7a "consumed
   when the link's response is finalized").

Answer validation scope (brief: "Validate submitted answers against the survey's
question set"): we reject answer keys that are not question ids in the survey, and at
finalize we enforce the required-question gate via ``apps.surveys.logic.
required_question_ids`` (the frozen Python evaluator, ADR-004). We do NOT re-run the full
survey-schema per-type answer-shape validation here — that is a deliberate bound (the
answer-shape doc in survey-schema.json is explicitly NON-VALIDATING); the question-set +
required gate is the contract-named check.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.surveys.enums import SurveyStatus
from apps.surveys.logic import required_question_ids
from apps.surveys.models import Survey

from .models import Display, Response, SurveyLink

# --------------------------------------------------------------------------- #
#  edit_token — signed, short-lived, single-response scope (ADR-009)           #
# --------------------------------------------------------------------------- #
# Namespaced salt so an edit_token can never be confused with any other signed value.
_EDIT_TOKEN_SALT = "opensurvey.responses.edit_token"
# "Short-lived" bound for abandoned partials (REVIEW.md §3.5). 24h is generous enough for a
# respondent to finish a started survey while still bounding stale tokens.
EDIT_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24


def make_edit_token(response_id) -> str:
    """Sign ``response_id`` into a short-lived, single-response ``edit_token`` (ADR-009).

    No DB column: the token is a self-describing ``TimestampSigner`` value. The salt
    namespaces it; ``verify_edit_token`` checks both the signature and the age.
    """
    return signing.TimestampSigner(salt=_EDIT_TOKEN_SALT).sign(str(response_id))


def verify_edit_token(token: str, response_id) -> bool:
    """True iff ``token`` is a valid, unexpired ``edit_token`` for THIS ``response_id``.

    Fails closed on a bad signature, expiry past ``EDIT_TOKEN_MAX_AGE_SECONDS``, or a
    token minted for a DIFFERENT response (single-response scope — REVIEW.md §3.5).
    """
    if not token:
        return False
    try:
        signed_id = signing.TimestampSigner(salt=_EDIT_TOKEN_SALT).unsign(
            token, max_age=EDIT_TOKEN_MAX_AGE_SECONDS
        )
    except signing.BadSignature:
        return False
    return signed_id == str(response_id)


def edit_token_expiry(reference=None):
    """The ``expires_at`` for a freshly-minted edit_token (now + max age).

    Returned in the ``PublicResponseHandle`` so the runtime knows when to stop trusting
    its token. ``reference`` lets callers pin the base time (defaults to ``now``).
    """
    base = reference or timezone.now()
    return base + timedelta(seconds=EDIT_TOKEN_MAX_AGE_SECONDS)


# --------------------------------------------------------------------------- #
#  Typed errors (mapped to HTTP codes in the views)                            #
# --------------------------------------------------------------------------- #
class SurveyClosedError(Exception):
    """A published survey has hit a ``close_on`` auto-close condition (→ 410).

    openapi POST-create 410 covers "survey closed (cap/date)" (models.md §2.6 ``close_on``):
      - ``by_date``: the close timestamp has passed.
      - ``by_response_cap``: FINISHED responses have reached the cap (survey-schema.json:
        "by_response_cap closes once finished responses reach the cap").
    The survey row keeps ``status=published`` (auto-close is condition-driven, not a stored
    status flip in v1) — the GET definition still serves so the runtime can show a closed
    message, but NEW responses are refused.
    """


class LinkGoneError(Exception):
    """A single-use link is consumed or expired (→ 410)."""


class LinkRequiredError(Exception):
    """The survey is single_use but no (valid) link_token was supplied (→ 410).

    A single_use survey is "not accessible without a link"; we map the missing/invalid
    link to 410 (the openapi 410 family for single-use links) rather than 404, so the
    runtime can distinguish "no such survey" from "this survey needs a link".
    """


class AnswerValidationError(Exception):
    """Submitted answers reference question ids that are not in the survey (→ 400).

    Carries ``unknown_keys`` so the view can surface a precise DRF-style error body.
    """

    def __init__(self, unknown_keys: list[str]) -> None:
        super().__init__("Answer payload references unknown question ids.")
        self.unknown_keys = unknown_keys


class RequiredAnswersMissing(Exception):
    """Finalize attempted while required (incl. logic-required) answers are missing (→ 400)."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("Required answers are missing.")
        self.missing = missing


# --------------------------------------------------------------------------- #
#  Survey lookup — public runtime serves ONLY published surveys                #
# --------------------------------------------------------------------------- #
def get_published_survey(survey_id) -> Survey | None:
    """Return the ``published`` Survey for ``survey_id`` (with workspace chain), else None.

    The public runtime must NOT leak draft/paused/closed surveys (openapi: "404 unless
    status=published"). A bad UUID, a missing survey, or any non-published status all
    return None so the view 404s uniformly (no status oracle).
    """
    try:
        survey = Survey.objects.select_related(
            "environment", "environment__workspace"
        ).get(pk=survey_id)
    except (Survey.DoesNotExist, ValueError):
        return None
    if survey.status != SurveyStatus.PUBLISHED:
        return None
    return survey


def assert_accepting_responses(survey: Survey) -> None:
    """Raise ``SurveyClosedError`` (→ 410) if ``survey.close_on`` has auto-closed it.

    Checked on the CREATE path only (openapi POST-create 410 "survey closed (cap/date)").
    GET-definition and displays still work on an auto-closed survey (the runtime needs the
    definition to render a "closed" message); only NEW responses are refused. Reads
    ``close_on`` = ``{by_date?: ISO8601, by_response_cap?: int}`` (models.md §2.6); both,
    either, or neither may be set. ``by_response_cap`` counts FINISHED responses
    (survey-schema.json closeConditions description).
    """
    close_on = survey.close_on or {}
    by_date = close_on.get("by_date")
    if by_date:
        from django.utils.dateparse import parse_datetime

        deadline = parse_datetime(by_date)
        if deadline is not None and deadline <= timezone.now():
            raise SurveyClosedError("This survey is closed.")

    cap = close_on.get("by_response_cap")
    if cap is not None:
        finished_count = Response.objects.filter(survey=survey, finished=True).count()
        if finished_count >= cap:
            raise SurveyClosedError("This survey has reached its response limit.")


# --------------------------------------------------------------------------- #
#  link_token → SurveyLink resolution (the ONLY trusted contact source)        #
# --------------------------------------------------------------------------- #
def _link_is_expired(link: SurveyLink) -> bool:
    return link.expires_at is not None and link.expires_at <= timezone.now()


def resolve_link(survey: Survey, link_token: str | None) -> SurveyLink | None:
    """Resolve + validate a ``link_token`` against ``survey``'s SurveyLink rows.

    Returns the SurveyLink when a usable token is given, ``None`` when no token is given
    on an OPEN (non-single-use) survey. Raises:
      - ``LinkRequiredError`` (→ 410): ``survey.single_use`` but ``link_token`` is missing.
      - ``LinkGoneError``     (→ 410): the token is unknown for this survey, already
        consumed (single-use), or expired.

    Token→link is matched within THIS survey only (a token for another survey is "gone").
    The caller derives contact association from the returned link's ``contact_id`` — never
    from client input (reconciled D-fix; PublicResponseCreate description).
    """
    if not link_token:
        if survey.single_use:
            raise LinkRequiredError(
                "This survey requires a personalized link to access."
            )
        return None

    try:
        link = SurveyLink.objects.get(survey=survey, token=link_token)
    except SurveyLink.DoesNotExist as exc:
        raise LinkGoneError("This survey link is invalid or has expired.") from exc

    if link.consumed_at is not None:
        raise LinkGoneError("This survey link has already been used.")
    if _link_is_expired(link):
        raise LinkGoneError("This survey link has expired.")
    return link


def contact_id_from_link(link: SurveyLink | None):
    """Server-derived contact association: ``link.contact_id`` or ``None``.

    This is the SOLE trusted source of a public response's contact (ADR-009 / rbac.md
    §11). A client-supplied contact_id is NEVER consulted.
    """
    return link.contact_id if link is not None else None


# --------------------------------------------------------------------------- #
#  Answer validation against the survey's question set                         #
# --------------------------------------------------------------------------- #
def _question_ids(survey: Survey) -> set[str]:
    return {q["id"] for q in (survey.questions or []) if isinstance(q, dict) and "id" in q}


def _hidden_field_ids(survey: Survey) -> set[str]:
    """The declared survey-level hidden-field ids (models.md / #/$defs/hiddenFields).

    Hidden fields are captured from the URL into ``Response.data`` at runtime (so recall +
    logic can read them), so their ids are ACCEPTED response-data keys alongside question
    ids. The per-question ``hidden`` type already lives in ``survey.questions`` and is covered
    by ``_question_ids``."""
    return {
        hf["id"]
        for hf in (survey.hidden_fields or [])
        if isinstance(hf, dict) and "id" in hf
    }


def validate_answer_keys(survey: Survey, data: dict[str, Any]) -> None:
    """Reject answer keys that are not question ids OR declared hidden-field ids in ``survey``
    (brief: "validate against the survey's question set"). Raises ``AnswerValidationError``
    listing the unknown keys.

    An empty/partial map is fine (save-as-you-go). We validate KEYS (the question set + the
    survey-level hidden fields, which the runtime captures from the URL), not per-type answer
    SHAPES — that boundary is documented in the module docstring.
    """
    if not data:
        return
    known = _question_ids(survey) | _hidden_field_ids(survey)
    unknown = sorted(k for k in data.keys() if k not in known)
    if unknown:
        raise AnswerValidationError(unknown)


def _survey_dict(survey: Survey) -> dict[str, Any]:
    """The evaluator-shaped dict (``questions`` + ``endings``) for the Python logic engine."""
    return {"questions": survey.questions or [], "endings": survey.endings or []}


def missing_required(survey: Survey, data: dict[str, Any]) -> list[str]:
    """Required question ids (static + logic-required) that lack a non-empty answer.

    Uses the frozen ``required_question_ids`` evaluator (ADR-004) so the server's finalize
    gate matches the runtime's. "Answered" = the key is present and non-empty (mirrors the
    evaluator's ``_is_submitted`` rule).
    """
    answers = data or {}
    required = required_question_ids(_survey_dict(survey), answers)
    missing = []
    for qid in required:
        value = answers.get(qid)
        is_empty = (
            value is None
            or value == ""
            or (isinstance(value, list | tuple | dict) and len(value) == 0)
        )
        if qid not in answers or is_empty:
            missing.append(qid)
    return sorted(missing)


# --------------------------------------------------------------------------- #
#  Create / patch / finalize                                                   #
# --------------------------------------------------------------------------- #
def create_response(
    *,
    survey: Survey,
    data: dict[str, Any] | None,
    language: str,
    link: SurveyLink | None,
    meta: dict[str, Any] | None,
) -> Response:
    """Create a (partial) Response for ``survey`` and bind it to ``link`` if present.

    Contact association is taken from ``link`` ONLY (server-derived). ``single_use_token``
    is stamped from the link's token so the consumed token is also tracked on the Response
    (models.md §2.7). Answer keys are validated against the question set before insert.
    Must be called with the tenant schema ACTIVE.
    """
    validate_answer_keys(survey, data or {})

    with transaction.atomic():
        response = Response.objects.create(
            survey=survey,
            data=data or {},
            finished=False,
            contact_id=contact_id_from_link(link),
            language=language or "default",
            meta=meta or {},
            single_use_token=(link.token if link is not None else None),
        )
        if link is not None:
            # Bind the link to the response it produced (models.md §2.7a). NOT yet consumed —
            # consumption happens on finalize (single-use semantics).
            link.response = response
            link.save(update_fields=["response", "updated_at"])

    # Fire the response.created webhook (best-effort side effect; never breaks the submission).
    _fire_response_event(response, finished=False)
    return response


def patch_response(*, response: Response, data: dict[str, Any], language: str | None) -> Response:
    """Merge ``data`` into ``response.data`` (save-as-you-go). Idempotent per question key.

    Keys are validated against the survey's question set; a bad key → ``AnswerValidationError``
    (→ 400) and nothing is written. A finished response is not re-opened here (the runtime
    only patches in-progress responses); we still allow the merge so a late autosave is not
    lost, but ``finished`` is untouched.
    """
    validate_answer_keys(response.survey, data or {})

    merged = dict(response.data or {})
    merged.update(data or {})
    response.data = merged
    update_fields = ["data", "updated_at"]
    if language:
        response.language = language
        update_fields.append("language")
    with transaction.atomic():
        response.save(update_fields=update_fields)
    return response


def finalize_response(*, response: Response) -> Response:
    """Mark ``response`` finished and consume its single-use link, atomically.

    Enforces the required-answer gate (static + logic-required) via ``missing_required``;
    a gap → ``RequiredAnswersMissing`` (→ 400) and nothing changes. On success, in ONE
    transaction: set ``finished=True`` + ``finished_at=now``, and if this response was
    created via a SurveyLink, set that link's ``consumed_at`` (models.md §2.7a) so a
    single-use link cannot be reused. Idempotent: finalizing an already-finished response
    is a no-op success.
    """
    if response.finished:
        return response

    missing = missing_required(response.survey, response.data or {})
    if missing:
        raise RequiredAnswersMissing(missing)

    with transaction.atomic():
        response.finished = True
        response.finished_at = timezone.now()
        response.save(update_fields=["finished", "finished_at", "updated_at"])
        # Consume any single-use link tied to this response (the reverse FK `link`).
        link = SurveyLink.objects.filter(response=response, consumed_at__isnull=True).first()
        if link is not None:
            link.consumed_at = timezone.now()
            link.save(update_fields=["consumed_at", "updated_at"])
        # Survey-quota enforcement (additive hook). AFTER finished=True is saved (so the count
        # below sees this row too — though we exclude it for safe, txn-visibility-independent
        # semantics) and INSIDE the same transaction. Marks this response over-quota + records
        # the routed ending when it is the (limit+1)th matching finished response for some quota.
        _apply_quotas(response)

    # Fire the response.finished webhook (best-effort side effect).
    _fire_response_event(response, finished=True)
    # Dispatch survey follow-ups (email-on-finish; best-effort side effect, parallel to the
    # webhook hook). Fired AFTER the webhook so a follow-up problem in eager test mode never
    # masks the webhook path; dispatch_follow_ups itself swallows every error.
    _fire_follow_ups(response)
    return response


def _fire_response_event(response: Response, *, finished: bool) -> None:
    """Dispatch response.created (finished=False) or response.finished (finished=True).

    Local imports avoid an app-loading cycle (responses must not import webhooks at load time).
    """
    from apps.webhooks.enums import WebhookEvent
    from apps.webhooks.events import response_payload
    from apps.webhooks.services import dispatch_event

    event = WebhookEvent.RESPONSE_FINISHED if finished else WebhookEvent.RESPONSE_CREATED
    dispatch_event(event=event, payload=response_payload(response))


def _fire_follow_ups(response: Response) -> None:
    """Dispatch the survey's follow-up automations for a finalized ``response``.

    Local import avoids an app-loading cycle (responses must not import followups at load
    time). ``dispatch_follow_ups`` is itself best-effort (never raises), but we guard here too
    so an import-time issue can never break finalize.
    """
    try:
        from apps.followups.services import dispatch_follow_ups

        dispatch_follow_ups(response)
    except Exception:  # noqa: BLE001 — follow-up dispatch must never break the submission.
        pass


# --------------------------------------------------------------------------- #
#  Quota enforcement (the finalize hook — additive, server-only)               #
# --------------------------------------------------------------------------- #
def _apply_quotas(response: Response) -> None:
    """Enforce survey response quotas on a freshly-finalized ``response`` (in the finalize txn).

    OVER-QUOTA SEMANTICS (documented + frozen): a quota with ``limit`` N accepts the FIRST N
    matching finished responses; the (N+1)th matching respondent is OVER-QUOTA. Concretely, this
    response is over-quota for a quota iff (a) its final ``Response.data`` MATCHES the quota's
    ``condition`` (evaluated by the SAME frozen evaluator as logic — ``_eval_condition`` — for
    ZERO drift; against ``Response.data`` ONLY, no variable replay) AND (b) the count of OTHER
    already-finished responses that ALSO match the condition has reached ``limit``. Quotas are
    scanned in array order; the FIRST matching-and-full quota wins (mirroring logic's
    ``_first_matching_nav_action`` first-match idiom), and its ``endingId`` (or None) is recorded.

    EFFICIENCY: only finished responses are pulled, fetching ONLY ``id`` + ``data`` (``.only``),
    excluding this response. The condition match is a Python-side eval over each row's ``data``
    (a quota condition may reference any answer, so it can't be a pure SQL filter); the scan
    EARLY-EXITS as soon as ``limit`` matches are seen, so a full survey only ever scans ``limit+1``
    matching rows in the worst case. This is an O(finished-responses) Python scan per matching
    quota — bounded by the early-exit; noted as the deliberate cost (no per-answer SQL index).

    CONCURRENCY (noted, not guarded): two finalizes racing at N-1 matches can both see < limit and
    neither flips over-quota, so a quota can be exceeded by the number of concurrent finalizes.
    The brief permits this ("bound it, note any N+1"); we do NOT take a row lock (it would serialize
    every finalize on a high-traffic survey). Documented as a known soft-cap race.

    Best-effort: any error is swallowed (a quota problem must never break the respondent's
    submission). Idempotent: a re-finalize recomputes the same state (pure function of the
    finished responses + the survey definition). A survey with no quotas EARLY-RETURNS (the
    byte-identical guarantee — no extra query, no field write).
    """
    try:
        survey = response.survey
        quotas = survey.quotas or []
        if not quotas:
            return  # no quotas -> behavior byte-identical to before this feature.

        from apps.surveys.logic import _eval_condition

        this_data = response.data or {}
        ending_ids = {
            e["id"]
            for e in (survey.endings or [])
            if isinstance(e, dict) and "id" in e
        }

        matched_quota: dict | None = None
        for quota in quotas:
            if not isinstance(quota, dict):
                continue
            condition = quota.get("condition")
            limit = quota.get("limit")
            if not isinstance(condition, dict) or not isinstance(limit, int) or limit < 1:
                continue
            # This response must itself match the quota to count toward it / be over it.
            try:
                if not _eval_condition(condition, this_data):
                    continue
            except Exception:  # noqa: BLE001 — a malformed condition never breaks finalize.
                continue

            # Count OTHER finished responses that match. Early-exit at `limit` (we only need to
            # know whether the cap is already reached, not the exact total). Excluding this row
            # makes the rule independent of in-txn visibility quirks: "others' matches >= limit".
            others = (
                Response.objects.filter(survey=survey, finished=True)
                .exclude(pk=response.pk)
                .only("id", "data")
                .iterator()
            )
            matching_others = 0
            full = False
            for other in others:
                try:
                    if _eval_condition(condition, other.data or {}):
                        matching_others += 1
                        if matching_others >= limit:
                            full = True
                            break
                except Exception:  # noqa: BLE001
                    continue
            if full:
                matched_quota = quota
                break  # first matching-and-full quota wins (array order).

        if matched_quota is None:
            return  # within every matching quota's limit -> not over-quota.

        ending_id = matched_quota.get("endingId")
        # Defensive: only record an endingId that resolves to a real ending; otherwise fall back
        # to the built-in screen (validate_quotas already rejects an unknown endingId on save, so
        # this only guards a quota authored before an ending was deleted out-of-band).
        if ending_id is not None and ending_id not in ending_ids:
            ending_id = None
        response.over_quota = True
        response.quota_full_ending_id = ending_id
        response.save(update_fields=["over_quota", "quota_full_ending_id", "updated_at"])
    except Exception:  # noqa: BLE001 — quota enforcement must never break the submission.
        import logging

        logging.getLogger(__name__).exception(
            "quota enforcement failed (swallowed; finalize unaffected)"
        )


# --------------------------------------------------------------------------- #
#  Display tracking (view → start → finish funnel)                             #
# --------------------------------------------------------------------------- #
def record_display(*, survey: Survey, link: SurveyLink | None) -> Display:
    """Record a display/impression for ``survey`` (models.md §2.8).

    Contact association is server-derived from ``link`` (the only trusted source), exactly
    like response create. Must be called with the tenant schema ACTIVE.
    """
    return Display.objects.create(
        survey=survey,
        contact_id=contact_id_from_link(link),
    )
