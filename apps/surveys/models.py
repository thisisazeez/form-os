"""
surveys — TENANT schema (TENANT_APPS). Survey definitions (contracts/models.md §2.6).

Question definitions + declarative logic live as JSON validated against
contracts/survey-schema.json (draft 2020-12) — the single source of truth (ADR-004).

TENANT-schema model: FK(Environment) is tenant→tenant (OK). `created_by_user_id` is a plain
UUIDField — NO FK (cross-schema, ADR-002) — resolved via hydrate_users().

JSON SHAPE IS NOT DB-ENFORCED (models.md §2.6): every write path (Wave 2c DRF serializer +
jsonschema) MUST validate questions / logic / languages / styling / welcome_card / endings /
close_on against survey-schema.json, plus in-code checks (unique question ids; no backward
jumpTo; jumpTo/endSurvey targets resolve to a later question id or a declared endings[].id;
operator/type validity). Models only persist the JSON.
"""
from django.db import models

from apps.common.hydration import UserHydrationManager
from apps.common.models import TimeStampedUUIDModel
from apps.workspaces.models import Environment

from .enums import SurveyStatus, SurveyType


class Survey(TimeStampedUUIDModel):
    """A survey definition (models.md §2.6). `id` appears in the public link
    /<org_slug>/s/<survey.id>. Public runtime serves only `published`."""

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="surveys",
    )
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=SurveyStatus.choices, default=SurveyStatus.DRAFT
    )

    # --- JSON columns (validated app-side against survey-schema.json; never DB-enforced) ---
    # Ordered array; validated against #/$defs/questions.
    questions = models.JSONField()
    # Survey-level logic is RESERVED/optional in v1 (logic is embedded per-question:
    # questions[].logic). Defaults to []. Callable default (mutable) per Django rules.
    logic = models.JSONField(default=list)
    # Array of language objects [{code, label, default}]; validated against #/$defs/surveyLanguages.
    languages = models.JSONField(default=list)
    # Theming object; validated against #/$defs/styling.
    styling = models.JSONField(default=dict)
    # Optional welcome/intro screen shown before the first question; #/$defs/welcomeCard.
    # null = no welcome card.
    welcome_card = models.JSONField(null=True)
    # Ordered array of ending/thank-you screens; items = #/$defs/ending. LOAD-BEARING for the
    # logic engine: a logic jumpTo.target or endSurvey.endingId may reference an ending id, so
    # endings MUST be persisted server-side for the jump-target validator (ADR-004) to resolve
    # those ids. May be empty ([]) — runtime shows a default ending.
    endings = models.JSONField(default=list)
    # Auto-close conditions {by_date?: ISO8601, by_response_cap?: int}; #/$defs/closeConditions.
    close_on = models.JSONField(null=True)
    # Survey-level hidden fields [{id, name?}]; validated against #/$defs/hiddenFields. Each
    # entry declares a URL query-param key captured into Response.data at runtime, where it
    # becomes available to recall/piping + logic. Defaults to [] (no hidden fields). Callable
    # default (mutable) per Django rules. Parallel to the per-question `hidden` type.
    hidden_fields = models.JSONField(default=list)
    # Survey follow-up automations [{id, name?, trigger, action}]; validated against
    # #/$defs/followUps. On response finalize, each follow-up's trigger (optionally a condition
    # over the answers) is evaluated and, when it fires, an email is rendered (recall-resolved
    # subject/body) + enqueued via the existing email infra. The DELIVERY ledger lives in
    # apps.followups (FollowUpDelivery); these are the DEFINITIONS only. Defaults to [] (no
    # follow-ups). Callable default (mutable) per Django rules. Mirrors `endings`.
    follow_ups = models.JSONField(default=list)
    # Question-grouping overlay [{id, title?, questionIds:[...], randomize?}]; validated against
    # #/$defs/block + in-code cross-ref checks (apps.surveys.blocks_validation). ADDITIVE +
    # backward-compatible: an absent/empty list == today's exact FLAT behavior. Blocks NEVER
    # move questions inside the schema and NEVER reorder `questions[]` (the flat array stays the
    # single source of truth for flow + logic); a block is a DISPLAY GROUPING plus an optional
    # WITHIN-BLOCK presentation randomization (seeded per-response at runtime). DEFERRED: block-
    # level logic. Defaults to [] (no blocks). Callable default (mutable) per Django rules.
    # Mirrors `endings`.
    blocks = models.JSONField(default=list)
    # Author-defined survey variables [{id, name, type:"number"|"text", initialValue}]; validated
    # against #/$defs/variable + in-code cross-ref checks (unique ids across the question/ending/
    # hidden-field/variable namespace; every calculate.variableId resolves; type-compatible
    # operation; ref values resolve). ADDITIVE + backward-compatible: an absent/empty list == no
    # variable replay == today's exact behavior. Variables are a typed mutable value seeded from
    # `initialValue`, mutated by the NEW `calculate` logic action as the respondent progresses, and
    # readable by recall/piping + conditions (variables share the recall/condition id namespace).
    # Their live values are NOT persisted into Response.data — they are recomputed deterministically
    # at runtime by replaying the matching calculate actions (apps.surveys.logic.compute_variables +
    # its TS twin). Callable default (mutable) per Django rules. Mirrors `endings`.
    variables = models.JSONField(default=list)
    # Response quotas [{id, name, limit, condition, endingId?}]; validated against #/$defs/quota +
    # in-code cross-ref checks (apps.surveys.quotas_validation: unique quota ids; endingId resolves
    # to a declared endings[].id; condition leftQuestionId refs resolve with a type-valid operator).
    # ADDITIVE + backward-compatible: an absent/empty list == today's exact behavior (no quota
    # enforcement). At response finalize, each quota's condition is evaluated against the final
    # Response.data by the SAME frozen evaluator as logic (apps.surveys.logic._eval_condition); if it
    # matches AND `limit` matching FINISHED responses already exist, the response is marked
    # over-quota and the runtime routes the respondent to `endingId` (or a built-in 'quota full'
    # screen). NOT exposed on the public runtime serializer (quota conditions are server-only).
    # Callable default (mutable) per Django rules. Mirrors `endings`.
    quotas = models.JSONField(default=list)

    # --- Website/App (in-product) survey fields (FEATURE_PARITY WS-11) ---------------------- #
    # Delivery surface. DEFAULT "link" so EVERY existing survey + the entire current link flow is
    # byte-identical (the new public widget endpoints + matching rules ONLY act on type=="app").
    # An "app" survey is shown by the embeddable JS widget, triggered by ActionClass events.
    survey_type = models.CharField(
        max_length=8,
        choices=SurveyType.choices,
        default=SurveyType.LINK,
        help_text="link (default; shareable URL) or app (in-product widget survey).",
    )
    # For app surveys: the ActionClass ids (by VALUE, not FK — mirrors logic referencing ending
    # ids) whose firing should display this survey. A JSON list of UUID strings; validated app-side
    # (each must resolve to an ActionClass in this survey's environment). Empty == never triggered.
    # Callable default (mutable) per Django rules. Ignored for link surveys.
    triggers = models.JSONField(default=list)
    # For app surveys: display rules {displayLimit?: int>=1 (max times shown to one user),
    # displayPercentage?: number 0-100 (random show gate)}. Validated app-side. {} == no per-survey
    # caps (still subject to the workspace recontact cooldown). Callable default (mutable) per Django
    # rules. Ignored for link surveys. NOTE: recontact COOLDOWN is workspace-level
    # (Workspace.recontact_days), NOT here — it is cross-survey, so it lives on the workspace.
    display_options = models.JSONField(default=dict)

    # If True, each response link is single-use (token consumed on finalize). See responses.
    single_use = models.BooleanField(default=False)

    # Privacy: when True the public response path stores the respondent IP (meta.ip);
    # when False (default) the IP is NOT captured at all. Off by default is GDPR-friendly
    # and matches the Formbricks default. IP is PII (rbac.md §11) — redacted on read.
    capture_ip = models.BooleanField(default=False)

    published_at = models.DateTimeField(null=True, blank=True)

    # Creator (public User PK). NO FK — cross-schema rule (ADR-002).
    created_by_user_id = models.UUIDField(null=True)

    objects = UserHydrationManager()

    class Meta:
        db_table = "surveys_survey"
        indexes = [
            models.Index(fields=["environment"], name="ix_survey_environment"),
            models.Index(fields=["status"], name="ix_survey_status"),
            models.Index(fields=["environment", "status"], name="ix_survey_env_status"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"
