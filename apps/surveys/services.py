"""
Survey services — create / update / delete + the status-transition state machine.

Single home for Survey business logic in the TENANT schema. DRF views call these. Two
invariants live HERE so no surface can bypass them:

  1. **Validation on write** (models.md §2.6 / ADR-004): create + replace run
     ``validate_survey_definition`` (jsonschema against survey-schema.json + in-code
     ``validate_survey``) before persisting. Invalid → ``SurveyValidationError`` (→ 400).

  2. **Status transitions** (openapi ``POST /surveys/{id}/status`` "Enforces valid
     transitions"): contracts/models.md §2.6 defines the SurveyStatus enum + ``published_at``
     = "first publish time" but does NOT enumerate the transition graph. We implement the
     defensible lifecycle and document it as the frozen-in-code matrix:

        draft     -> published
        published -> paused | closed
        paused    -> published | closed
        closed    -> (terminal — no transitions)

     ``published_at`` is set on the FIRST publish only (draft -> published); re-activating
     from paused does NOT reset it. A no-op transition to the SAME status is rejected (400),
     as is any edge not in the matrix (e.g. closed -> published, draft -> paused).

Typed errors map to HTTP codes in the view:
  - ``SurveyValidationError``  -> 400 (schema_errors / logic_errors body)
  - ``InvalidStatusTransition`` -> 400
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .enums import SurveyStatus, SurveyType
from .models import Survey
from .validators import SurveyValidationError, validate_survey_definition

__all__ = [
    "SurveyValidationError",
    "InvalidStatusTransition",
    "create_survey",
    "replace_survey",
    "delete_survey",
    "transition_status",
    "action_for_status",
    "ALLOWED_TRANSITIONS",
]


def _validate_app_survey_refs(environment, survey_type: str, triggers: list | None) -> None:
    """DB-backed cross-reference check for app-survey ``triggers`` (FEATURE_PARITY WS-11).

    The pure shape check (apps.surveys.app_survey_validation.validate_app_survey) already ran in
    ``validate_survey_definition``; here we add the part that needs the environment + a query:
    every trigger id must resolve to an ActionClass IN THIS survey's environment. Raises
    ``SurveyValidationError`` (→ 400) listing the unresolved ids.

    Only enforced for ``survey_type == "app"`` (a link survey ignores ``triggers``). An app
    survey with empty ``triggers`` is allowed (it simply never auto-displays — the author may be
    mid-build); we do NOT require >=1 trigger at save time. Local import avoids an app-loading
    cycle (surveys must not import actions at module load).
    """
    if survey_type != SurveyType.APP:
        return
    triggers = triggers or []
    if not triggers:
        return

    from apps.actions.models import ActionClass

    existing = set(
        str(aid)
        for aid in ActionClass.objects.filter(
            environment=environment, id__in=triggers
        ).values_list("id", flat=True)
    )
    unresolved = sorted(str(t) for t in triggers if str(t) not in existing)
    if unresolved:
        raise SurveyValidationError(
            "Survey triggers reference unknown action classes.",
            logic_errors=[
                {
                    "code": "app_survey_unknown_trigger",
                    "message": f"trigger {t!r} does not resolve to an ActionClass in this environment",
                    "id": t,
                }
                for t in unresolved
            ],
        )


class InvalidStatusTransition(Exception):
    """A requested survey status transition is not permitted by the lifecycle (→ 400)."""


# The frozen-in-code lifecycle (documented in the module docstring). Each key maps to the
# set of statuses it may transition TO. ``closed`` is terminal (empty set).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    SurveyStatus.DRAFT: {SurveyStatus.PUBLISHED},
    SurveyStatus.PUBLISHED: {SurveyStatus.PAUSED, SurveyStatus.CLOSED},
    SurveyStatus.PAUSED: {SurveyStatus.PUBLISHED, SurveyStatus.CLOSED},
    SurveyStatus.CLOSED: set(),
}

# Per-target-status RBAC action (openapi x-permission-note: dispatch can() on the TARGET
# status). All three are workspace `write` in rbac.md §6.
_STATUS_ACTION: dict[str, str] = {
    SurveyStatus.PUBLISHED: "publish_survey",
    SurveyStatus.PAUSED: "pause_survey",
    SurveyStatus.CLOSED: "close_survey",
}


def action_for_status(target_status: str) -> str:
    """The rbac action a status transition requires (dispatched on the TARGET status).

    ``draft`` is not a transition target via the status endpoint (a survey starts as draft
    and there is no "unpublish to draft" edge), so only published/paused/closed are mapped.
    Raises ``InvalidStatusTransition`` for any other target so the view fails closed before
    touching the DB.
    """
    try:
        return _STATUS_ACTION[target_status]
    except KeyError as exc:
        raise InvalidStatusTransition(
            f"{target_status!r} is not a valid status-transition target."
        ) from exc


def create_survey(
    *,
    environment,
    name: str,
    questions: list,
    languages: list,
    logic: list | None = None,
    styling: dict | None = None,
    welcome_card: dict | None = None,
    endings: list | None = None,
    close_on: dict | None = None,
    hidden_fields: list | None = None,
    follow_ups: list | None = None,
    blocks: list | None = None,
    variables: list | None = None,
    quotas: list | None = None,
    survey_type: str | None = None,
    triggers: list | None = None,
    display_options: dict | None = None,
    single_use: bool = False,
    capture_ip: bool = False,
    created_by_user_id=None,
) -> Survey:
    """Validate + create a draft Survey in ``environment``.

    The full definition is validated (jsonschema + in-code ``validate_survey``) BEFORE the
    insert; an invalid definition raises ``SurveyValidationError`` and nothing is written.
    A newly created survey always starts in ``draft`` (the status endpoint drives the rest
    of the lifecycle).

    APP-SURVEY FIELDS (FEATURE_PARITY WS-11; default to link/[]/{}): ``survey_type`` /
    ``triggers`` / ``display_options`` are validated for SHAPE by ``validate_survey_definition``
    and for trigger-RESOLUTION (each id is an ActionClass in this env) by
    ``_validate_app_survey_refs``. A link survey ignores triggers/display_options.

    Must be called with the tenant schema ACTIVE.
    """
    survey_type = survey_type or SurveyType.LINK
    triggers = triggers or []
    display_options = display_options or {}
    definition = {
        "name": name,
        "questions": questions,
        "logic": logic or [],
        "languages": languages,
        "styling": styling or {},
        "welcome_card": welcome_card,
        "endings": endings or [],
        "close_on": close_on,
        "hidden_fields": hidden_fields or [],
        "follow_ups": follow_ups or [],
        "blocks": blocks or [],
        "variables": variables or [],
        "quotas": quotas or [],
        "survey_type": survey_type,
        "triggers": triggers,
        "display_options": display_options,
    }
    validate_survey_definition(definition)
    _validate_app_survey_refs(environment, survey_type, triggers)

    with transaction.atomic():
        survey = Survey.objects.create(
            environment=environment,
            name=name,
            status=SurveyStatus.DRAFT,
            questions=questions,
            logic=logic or [],
            languages=languages,
            styling=styling or {},
            welcome_card=welcome_card,
            endings=endings or [],
            close_on=close_on,
            hidden_fields=hidden_fields or [],
            follow_ups=follow_ups or [],
            blocks=blocks or [],
            variables=variables or [],
            quotas=quotas or [],
            survey_type=survey_type,
            triggers=triggers,
            display_options=display_options,
            single_use=single_use,
            capture_ip=capture_ip,
            created_by_user_id=created_by_user_id,
        )
    return survey


def replace_survey(
    *,
    survey: Survey,
    name: str,
    questions: list,
    languages: list,
    logic: list | None = None,
    styling: dict | None = None,
    welcome_card: dict | None = None,
    endings: list | None = None,
    close_on: dict | None = None,
    hidden_fields: list | None = None,
    follow_ups: list | None = None,
    blocks: list | None = None,
    variables: list | None = None,
    quotas: list | None = None,
    survey_type: str | None = None,
    triggers: list | None = None,
    display_options: dict | None = None,
    single_use: bool | None = None,
    capture_ip: bool | None = None,
) -> Survey:
    """Validate + replace a survey's definition (PUT semantics).

    The full definition is validated before persisting; invalid → ``SurveyValidationError``
    (nothing is written). ``environment`` and ``status`` are NOT mutable here (env is fixed
    at creation; status changes go through ``transition_status``). ``single_use`` is updated
    only when explicitly provided.

    APP-SURVEY FIELDS (FEATURE_PARITY WS-11): ``survey_type`` / ``triggers`` / ``display_options``
    are updated only when explicitly provided (``None`` = leave as-is, preserving PUT semantics
    for callers that don't know about the fields). The effective values (new-or-existing) are
    SHAPE-validated together with the rest of the definition and trigger-RESOLUTION-validated.
    """
    # Effective app-survey values: explicit value wins; else keep the survey's current value.
    eff_survey_type = survey_type if survey_type is not None else survey.survey_type
    eff_triggers = triggers if triggers is not None else (survey.triggers or [])
    eff_display_options = (
        display_options if display_options is not None else (survey.display_options or {})
    )
    definition = {
        "name": name,
        "questions": questions,
        "logic": logic or [],
        "languages": languages,
        "styling": styling or {},
        "welcome_card": welcome_card,
        "endings": endings or [],
        "close_on": close_on,
        "hidden_fields": hidden_fields or [],
        "follow_ups": follow_ups or [],
        "blocks": blocks or [],
        "variables": variables or [],
        "quotas": quotas or [],
        "survey_type": eff_survey_type,
        "triggers": eff_triggers,
        "display_options": eff_display_options,
    }
    validate_survey_definition(definition)
    _validate_app_survey_refs(survey.environment, eff_survey_type, eff_triggers)

    survey.name = name
    survey.questions = questions
    survey.logic = logic or []
    survey.languages = languages
    survey.styling = styling or {}
    survey.welcome_card = welcome_card
    survey.endings = endings or []
    survey.close_on = close_on
    survey.hidden_fields = hidden_fields or []
    survey.follow_ups = follow_ups or []
    survey.blocks = blocks or []
    survey.variables = variables or []
    survey.quotas = quotas or []
    update_fields = [
        "name", "questions", "logic", "languages", "styling",
        "welcome_card", "endings", "close_on", "hidden_fields", "follow_ups",
        "blocks", "variables", "quotas", "updated_at",
    ]
    if survey_type is not None:
        survey.survey_type = survey_type
        update_fields.append("survey_type")
    if triggers is not None:
        survey.triggers = triggers
        update_fields.append("triggers")
    if display_options is not None:
        survey.display_options = display_options
        update_fields.append("display_options")
    if single_use is not None:
        survey.single_use = single_use
        update_fields.append("single_use")
    if capture_ip is not None:
        survey.capture_ip = capture_ip
        update_fields.append("capture_ip")

    with transaction.atomic():
        survey.save(update_fields=update_fields)
    return survey


def delete_survey(*, survey: Survey) -> None:
    """Delete a survey (cascade removes its responses/links/displays — model FKs)."""
    survey.delete()


def transition_status(*, survey: Survey, target_status: str) -> Survey:
    """Transition a survey to ``target_status`` per the frozen lifecycle matrix.

    Raises ``InvalidStatusTransition`` (→ 400) if the edge is not allowed (incl. a no-op
    transition to the same status, and any transition out of ``closed``). Sets
    ``published_at`` on the FIRST publish only (draft -> published; not on republish from
    paused).
    """
    current = survey.status
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target_status not in allowed:
        raise InvalidStatusTransition(
            f"Cannot transition survey from {current!r} to {target_status!r}."
        )

    survey.status = target_status
    update_fields = ["status", "updated_at"]
    # First-publish timestamp: only set when going draft -> published and not previously set.
    if target_status == SurveyStatus.PUBLISHED and survey.published_at is None:
        survey.published_at = timezone.now()
        update_fields.append("published_at")

    with transaction.atomic():
        survey.save(update_fields=update_fields)

    # Fire the matching webhook event (frozen WebhookEvent set: published / closed). Best-effort
    # side effect — dispatch_event never raises into this transition. Audit is written by the
    # view/HTMX surface (it owns the actor/request), keeping this service surface-agnostic.
    _fire_survey_status_event(survey, target_status)
    return survey


def _fire_survey_status_event(survey, target_status: str) -> None:
    """Dispatch the webhook event that corresponds to a survey status transition (if any).

    Only ``published`` and ``closed`` map to a frozen WebhookEvent value; paused has no event.
    Local imports avoid an app-loading cycle (surveys must not import webhooks at module load).
    """
    from apps.webhooks.enums import WebhookEvent
    from apps.webhooks.events import survey_payload
    from apps.webhooks.services import dispatch_event

    event_map = {
        SurveyStatus.PUBLISHED: WebhookEvent.SURVEY_PUBLISHED,
        SurveyStatus.CLOSED: WebhookEvent.SURVEY_CLOSED,
    }
    event = event_map.get(target_status)
    if event is not None:
        dispatch_event(event=event, payload=survey_payload(survey))
