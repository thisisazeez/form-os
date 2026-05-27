"""
Public widget service layer (FEATURE_PARITY WS-11) — the in-product (website/app) survey
matching engine consumed by the embeddable JS widget.

This is the business-logic home for the PUBLIC, unauthenticated widget path. The DRF public
views (apps.actions.public_views) call these so no surface bypasses the matching invariants.

PERSON / userId IDENTIFICATION (documented design choice — the brief grants the call):
  The widget supplies a stable, client-controlled ``userId`` (the host app's user identifier).
  We map it to a per-Environment ``Contact`` via ``Contact.external_id`` — the model docstring
  defines external_id as exactly that ("Caller-supplied stable id (e.g. CRM id)"), and it has a
  per-env unique constraint (``uniq_contact_external_per_env``), so no attribute dance is needed.
  ``get_or_create_person(env, userId)`` upserts the Contact; the surrounding Display/Response
  rows then key off ``Contact.id`` naturally. (Alternative considered: a ``userId``
  ContactAttribute — rejected; external_id already IS this, with a unique index.)

ACTION RESOLUTION (track):
  A tracked action is matched to an ActionClass by ``config.key`` first, then ``name`` (both
  are unique-ish per env; name is unique). The pageview/click URL/selector matching is the
  WIDGET's job — it resolves which action fired client-side and sends us the action name/key.

MATCHING RULES (the canonical spec — also documented on the public endpoints). A published
``app``-type survey in ``environment`` is ELIGIBLE to show to a given person right now iff ALL:
  1. ``survey.status == published`` AND ``survey.survey_type == "app"``.
  2. (track only) a trigger action matches: ``actionClass.id ∈ survey.triggers``.
     (GET app-surveys skips this — it returns the catalog the widget then fires client-side.)
  3. displayLimit: the person has been shown THIS survey fewer than ``display_options.displayLimit``
     times (count this person's ``Display`` rows for this survey). Absent limit → no cap.
  4. recontact cooldown: at least ``Workspace.recontact_days`` days have passed since the person's
     LAST display of ANY app survey in this workspace. ``recontact_days IS NULL`` → no cooldown.
  5. displayPercentage: a random gate passes for ``display_options.displayPercentage`` (0..100).
     Absent → always passes. Deterministic-in-tests via the module-level ``_passes_percentage_gate``.

IMPRESSION TRACKING IS THE WIDGET'S JOB (not track's): the ``track`` endpoint does NOT create
Display rows. It returns the list of eligible surveys; the widget decides which to actually show
and then calls the dedicated app-survey impression endpoint
``POST /api/v1/<org>/public/environments/<env>/app-surveys/<survey_id>/displays`` (body {userId})
per shown survey (apps.actions.public_views.WidgetDisplayView → ``record_app_display``), which
records a Display KEYED TO THE userId-RESOLVED CONTACT. We use a DEDICATED endpoint (NOT the
existing ``POST /public/surveys/{id}/displays``) because that one derives contact_id from a
``link_token`` only — the widget has none, so its impressions would be anonymous and the
contact_id-keyed displayLimit + recontact-cooldown counters would NEVER fire. Otherwise displayLimit
would also over-count surveys the widget returned-but-didn't-show, hence the widget (not track)
records the impression. Submissions reuse the EXISTING public response create/patch/finalize loop
(the widget loads the full definition from the existing public survey-definition GET). Documented so
the widget agent wires it correctly.

DEFERRED (noted): segment/attribute TARGETING beyond the above rules; the React Native SDK
surface (this engine is WEB-shaped but model-agnostic, so RN could reuse it later).
"""
from __future__ import annotations

import random
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.contacts.models import Contact
from apps.responses.models import Display
from apps.surveys.enums import SurveyStatus, SurveyType
from apps.surveys.models import Survey

from .models import ActionClass


# --------------------------------------------------------------------------- #
#  Percentage gate — overridable in tests (monkeypatch to make it deterministic)
# --------------------------------------------------------------------------- #
def _passes_percentage_gate(pct: float | int) -> bool:
    """Return True with probability ``pct``/100. Wraps ``random`` so tests monkeypatch THIS
    function (not ``random``) to make the gate deterministic. ``pct`` is assumed validated to
    0..100 (the survey write-path validator enforced it); defensively clamps anyway."""
    if pct is None:
        return True
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    return random.random() * 100.0 < float(pct)


# --------------------------------------------------------------------------- #
#  Person (userId) identification — Contact.external_id per environment
# --------------------------------------------------------------------------- #
def get_or_create_person(environment, user_id: str) -> Contact:
    """Resolve (or create) the per-environment ``Contact`` for the widget's ``user_id``.

    Keyed on ``Contact.external_id`` (the model's caller-supplied stable id) within the env;
    the ``uniq_contact_external_per_env`` constraint makes this a clean upsert. Must be called
    with the tenant schema ACTIVE.
    """
    contact, _created = Contact.objects.get_or_create(
        environment=environment,
        external_id=user_id,
    )
    return contact


# --------------------------------------------------------------------------- #
#  Action resolution (track)
# --------------------------------------------------------------------------- #
def resolve_action_class(environment, action: str) -> ActionClass | None:
    """Resolve a tracked action name/key to an ActionClass in ``environment``.

    Match precedence: ``config.key`` (a ``code`` action's explicit SDK key) → ``name``. Returns
    None if nothing matches (the widget tracked an action with no server-side definition — the
    track call is still a 200 with an empty survey list, NOT an error).
    """
    if not action:
        return None
    # config.key match first (a code action may declare an explicit SDK key distinct from name).
    by_key = (
        ActionClass.objects.filter(environment=environment, config__key=action)
        .order_by("created_at", "id")
        .first()
    )
    if by_key is not None:
        return by_key
    return (
        ActionClass.objects.filter(environment=environment, name=action)
        .order_by("created_at", "id")
        .first()
    )


# --------------------------------------------------------------------------- #
#  Matching engine
# --------------------------------------------------------------------------- #
def _published_app_surveys(environment):
    """Published, app-type surveys in ``environment`` (the candidate set for matching)."""
    return Survey.objects.filter(
        environment=environment,
        status=SurveyStatus.PUBLISHED,
        survey_type=SurveyType.APP,
    ).order_by("created_at", "id")


def _display_count(survey: Survey, contact: Contact) -> int:
    """How many times THIS person has been shown THIS survey (the displayLimit counter)."""
    return Display.objects.filter(survey=survey, contact_id=contact.id).count()


def _last_app_display_at(environment, contact: Contact):
    """The most recent ``shown_at`` of ANY app-survey Display for this person in ``environment``.

    Drives the workspace recontact cooldown (cross-survey). Scoped to app-type surveys in the
    env (a link survey's impressions never consume the in-product cooldown). None == the person
    has never been shown an app survey → no cooldown applies.
    """
    row = (
        Display.objects.filter(
            contact_id=contact.id,
            survey__environment=environment,
            survey__survey_type=SurveyType.APP,
        )
        .order_by("-shown_at")
        .values_list("shown_at", flat=True)
        .first()
    )
    return row


def _within_recontact_cooldown(environment, workspace, contact: Contact) -> bool:
    """True if the person is STILL in the workspace recontact cooldown (→ show NOTHING).

    ``Workspace.recontact_days`` is the cooldown in days since the person's last app-survey
    display ANYWHERE in this workspace's env. NULL → no cooldown (never blocked). 0 → any prior
    display within the same instant blocks until the timestamp strictly passes (a 0-day floor
    still requires now > last_display, which is effectively immediate); practically 0 means "no
    wait". We treat days as a timedelta and compare to now.
    """
    recontact_days = getattr(workspace, "recontact_days", None)
    if recontact_days is None:
        return False  # no cooldown configured.
    last = _last_app_display_at(environment, contact)
    if last is None:
        return False  # never shown an app survey → not in cooldown.
    from datetime import timedelta

    cooldown_until = last + timedelta(days=recontact_days)
    return timezone.now() < cooldown_until


def _passes_per_survey_gates(survey: Survey, contact: Contact) -> bool:
    """Per-survey gates: displayLimit cap + displayPercentage random gate (rules 3 + 5).

    The workspace recontact cooldown (rule 4) is checked ONCE per request by the caller (it is
    cross-survey), not here.
    """
    options = survey.display_options or {}

    # Rule 3: displayLimit — count this person's displays of THIS survey.
    limit = options.get("displayLimit")
    if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1:
        if _display_count(survey, contact) >= limit:
            return False

    # Rule 5: displayPercentage — random gate (deterministic-in-tests via the module function).
    pct = options.get("displayPercentage")
    if pct is not None:
        if not _passes_percentage_gate(pct):
            return False

    return True


def eligible_app_surveys(
    *,
    environment,
    contact: Contact,
    action_class: ActionClass | None = None,
) -> list[Survey]:
    """The published app-surveys in ``environment`` eligible to show ``contact`` RIGHT NOW.

    Applies the canonical matching rules (see module docstring):
      - If ``action_class`` is given (the ``track`` path), ALSO require the survey's ``triggers``
        to include that action class id (rule 2). If ``action_class`` is None, the trigger filter
        is SKIPPED — every published app survey is a candidate, gated only by user-state. This
        None-means-catalog mode is used by the GET app-surveys endpoint; the ``track`` endpoint
        does NOT call here with None (an unmatched tracked action returns [] in ``track_action``,
        because there is no trigger to satisfy — see ``track_action``).
      - The workspace recontact cooldown (rule 4) is evaluated ONCE: if the person is still in
        cooldown, return [] immediately (no app survey shows).
      - Then per-survey displayLimit (rule 3) + displayPercentage (rule 5) gate each candidate.

    Returns the eligible Survey rows (ordered by created_at). Must be called with the tenant
    schema ACTIVE.
    """
    workspace = environment.workspace
    # Rule 4 (cross-survey): one cooldown check for the whole request.
    if _within_recontact_cooldown(environment, workspace, contact):
        return []

    candidates = list(_published_app_surveys(environment))

    eligible: list[Survey] = []
    action_id = str(action_class.id) if action_class is not None else None
    for survey in candidates:
        # Rule 2 (track only): the survey must be triggered by THIS action.
        if action_id is not None:
            triggers = survey.triggers or []
            if action_id not in [str(t) for t in triggers]:
                continue
        # Rules 3 + 5: per-survey caps/gate.
        if not _passes_per_survey_gates(survey, contact):
            continue
        eligible.append(survey)
    return eligible


# --------------------------------------------------------------------------- #
#  track — record(resolve) the action + return the surveys that should display
# --------------------------------------------------------------------------- #
def track_action(
    *,
    environment,
    user_id: str,
    action: str,
    attributes: dict[str, Any] | None = None,
    url: str | None = None,
) -> tuple[Contact, ActionClass | None, list[Survey]]:
    """Process a widget ``track`` call.

    Steps:
      1. Upsert the person (Contact by external_id == user_id) in the env.
      2. (optional) merge ``attributes`` onto the contact (so a code-action can carry traits).
      3. Resolve the action to an ActionClass (name/key) — None if undefined (still a 200).
      4. Return the eligible app-surveys (matching rules; trigger-filtered by the resolved action).

    Returns ``(contact, action_class, eligible_surveys)``. The endpoint does NOT create Display
    rows (the widget calls the existing /displays endpoint per shown survey — see module
    docstring). ``url`` is accepted for parity/forward-compat (pageview context) but not used for
    server-side matching (the widget resolves URL matches client-side). Must be called with the
    tenant schema ACTIVE.
    """
    with transaction.atomic():
        contact = get_or_create_person(environment, user_id)
        if attributes:
            _merge_attributes(environment, contact, attributes)
        action_class = resolve_action_class(environment, action)
        # A tracked action that does NOT resolve to an ActionClass fires NOTHING (no trigger to
        # match) — return empty WITHOUT delegating to eligible_app_surveys (whose action_class=None
        # branch means the GET "catalog" path, NOT "unmatched action"). This is the documented
        # distinction between the two endpoints.
        if action_class is None:
            surveys: list[Survey] = []
        else:
            surveys = eligible_app_surveys(
                environment=environment, contact=contact, action_class=action_class
            )
    return contact, action_class, surveys


def _merge_attributes(environment, contact: Contact, attributes: dict[str, Any]) -> None:
    """Best-effort merge of widget-supplied traits onto the contact (reuses the contacts upsert).

    Attributes are OPTIONAL context a code-action may carry (e.g. ``{plan: "pro"}``); we store
    them as ContactAttributes via the existing contacts service so they're segment-visible. Any
    bad value is swallowed (a trait problem must never break the widget's track call — the
    survey-matching is the contract, traits are a bonus). Reuses
    apps.contacts.services.update_contact_attributes (single home for attribute typing/validation).
    """
    try:
        from apps.contacts.services import update_contact_attributes

        attr_list = [{"key": str(k), "value": str(v)} for k, v in attributes.items()]
        update_contact_attributes(contact=contact, attributes=attr_list)
    except Exception:  # noqa: BLE001 — trait merge is best-effort; never break track.
        pass


# --------------------------------------------------------------------------- #
#  App-survey impression (display) — keyed by userId → Contact
# --------------------------------------------------------------------------- #
class AppSurveyNotFound(Exception):
    """The display target is not a published app survey in this environment (→ 404)."""


def record_app_display(*, environment, survey_id, user_id: str) -> Display:
    """Record a Display for an app survey shown to ``user_id`` (FEATURE_PARITY WS-11).

    WHY a dedicated path (not the existing /public/surveys/{id}/displays): that endpoint derives
    ``contact_id`` from a ``link_token`` ONLY, so an app-survey impression would be ANONYMOUS
    (contact_id=None) — which would make displayLimit + recontact-cooldown counts (both keyed on
    contact_id) NEVER fire. The widget has no link_token; it has a ``userId``. So the widget
    records app-survey impressions HERE, where we resolve the userId → Contact (the SAME person
    identity as track/app-surveys) and stamp ``Display.contact_id`` accordingly. The Display model
    + funnel are reused unchanged; only the contact-resolution source differs (userId, not link).

    Validates that ``survey_id`` is a PUBLISHED, ``app``-type survey in ``environment`` (a link
    survey or a foreign/unpublished survey → ``AppSurveyNotFound`` → 404 — no impression). Must be
    called with the tenant schema ACTIVE.
    """
    try:
        survey = Survey.objects.get(
            pk=survey_id,
            environment=environment,
            status=SurveyStatus.PUBLISHED,
            survey_type=SurveyType.APP,
        )
    except (Survey.DoesNotExist, ValueError) as exc:
        raise AppSurveyNotFound("App survey not found.") from exc

    with transaction.atomic():
        contact = get_or_create_person(environment, user_id)
        display = Display.objects.create(survey=survey, contact_id=contact.id)
    return display
