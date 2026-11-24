// ============================================================================
// onForm — Shared survey TypeScript types.
//
// Derived from contracts/survey-schema.json (the FROZEN Wave-0 contract; single
// source of truth for the Survey definition + answer shapes) and the PublicSurvey
// shape in contracts/openapi.yaml.
//
// These types describe the survey DEFINITION the runtime renders and the ANSWER
// shapes it captures. The logic evaluator intentionally operates on a looser
// `Json` view internally (see logic/evaluator.ts) — typing the schema-discriminated
// union strictly for evaluation fights the runtime and risks behavioral drift from
// the reference. The strict types here drive the renderers + the runtime island.
// ============================================================================

/** Loose JSON value used where the schema is intentionally open (config blobs, answers). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

// --------------------------------------------------------------------------- //
//  i18n                                                                       //
// --------------------------------------------------------------------------- //
/**
 * A localized display string: map of langCode -> text. MUST contain the
 * 'default' key (the fallback shown when a response language has no entry).
 * survey-schema.json #/$defs/i18nString.
 */
export interface I18nString {
  default: string;
  [langCode: string]: string;
}

/** Same as I18nString but the 'default' key is not required (optional subtitles/placeholders). */
export type I18nStringOptional = Record<string, string>;

// --------------------------------------------------------------------------- //
//  Languages / status / styling                                              //
// --------------------------------------------------------------------------- //
export interface Language {
  code: string;
  label?: string;
  default: boolean;
  enabled?: boolean;
}

export type SurveyStatus = "draft" | "published" | "paused" | "closed";

export interface Styling {
  brandColor?: string;
  questionColor?: string;
  backgroundColor?: string;
  buttonColor?: string;
  buttonTextColor?: string;
  background?: { type?: "color" | "image" | "none"; value?: string };
  logoUrl?: string;
  roundness?: number;
  hideProgressBar?: boolean;
  cardArrangement?: "straight" | "casual";
}

export interface WelcomeCard {
  enabled?: boolean;
  headline?: I18nString;
  html?: I18nStringOptional;
  buttonLabel?: I18nStringOptional;
}

export interface Ending {
  id: string;
  type: "endScreen" | "redirect";
  headline?: I18nStringOptional;
  subheader?: I18nStringOptional;
  buttonLabel?: I18nStringOptional;
  buttonLink?: string;
  redirectUrl?: string;
}

/**
 * A survey-level hidden field (survey-schema.json #/$defs/hiddenField). `id` is the
 * URL query-param key captured into Response.data at runtime AND the recall lookup key;
 * `name` is the human label shown in the builder. Parallel to the per-question `hidden`
 * type. See lib/recall.ts for the recall token format.
 */
export interface HiddenField {
  id: string;
  name?: string;
}

/**
 * Auto-close conditions (survey-schema.json #/$defs/closeConditions; model `close_on`).
 * Either/both/neither may be set. `by_date` is an ISO 8601 UTC timestamp; `by_response_cap`
 * closes the survey once finished responses reach the cap. The server enforces both
 * (apps/responses public path → 410). snake_case keys match the admin API + SurveyWrite body.
 */
export interface CloseConditions {
  by_date?: string;
  by_response_cap?: number;
}

// --------------------------------------------------------------------------- //
//  Question types                                                            //
// --------------------------------------------------------------------------- //
export type QuestionType =
  | "openText"
  | "singleSelect"
  | "multiSelect"
  | "pictureChoice"
  | "rating"
  | "nps"
  | "cta"
  | "consent"
  | "date"
  | "fileUpload"
  | "matrix"
  | "ranking"
  | "number"
  | "contactInfo"
  | "address"
  | "scheduleMeeting"
  | "hidden";

export interface Choice {
  id: string;
  label: I18nString;
  isOther?: boolean;
}

export interface PictureChoiceOption {
  id: string;
  imageUrl: string;
  label?: I18nStringOptional;
}

// --- per-type config shapes --- //
export interface OpenTextConfig {
  inputType?: "short" | "long";
  placeholder?: I18nStringOptional;
  minLength?: number;
  maxLength?: number;
}

export interface SingleSelectConfig {
  choices: Choice[];
  shuffle?: "none" | "all" | "exceptLast";
  display?: "list" | "dropdown";
}

export interface MultiSelectConfig {
  choices: Choice[];
  shuffle?: "none" | "all" | "exceptLast";
  minSelections?: number;
  maxSelections?: number;
}

export interface RatingConfig {
  range: number; // 3..10
  scale: "star" | "number" | "smiley";
  lowerLabel?: I18nStringOptional;
  upperLabel?: I18nStringOptional;
}

export interface NpsConfig {
  lowerLabel?: I18nStringOptional;
  upperLabel?: I18nStringOptional;
}

export interface CtaConfig {
  html?: I18nStringOptional;
  buttonLabel?: I18nStringOptional;
  buttonUrl?: string;
  dismissable?: boolean;
}

export interface ConsentConfig {
  label: I18nString;
  html?: I18nStringOptional;
}

export interface DateConfig {
  /**
   * Display format preference. NOTE: the native <input type="date"> always
   * emits/consumes the ISO `YYYY-MM-DD` value (the schema's answer shape); the
   * browser renders it per the user's locale and this preference is advisory.
   */
  format?: "YYYY-MM-DD" | "DD-MM-YYYY" | "MM-DD-YYYY";
  min?: string; // 'YYYY-MM-DD'
  max?: string; // 'YYYY-MM-DD'
}

export interface FileUploadConfig {
  allowMultiple?: boolean;
  maxFiles?: number;
  maxSizeMb?: number;
  /** e.g. ['.pdf', '.png']. Empty/absent = allow all (still server-validated). */
  allowedExtensions?: string[];
}

export interface NumberConfig {
  min?: number;
  max?: number;
  step?: number; // exclusiveMinimum: 0
  placeholder?: I18nStringOptional;
}

export interface MatrixConfig {
  rows: Choice[]; // minItems 1
  columns: Choice[]; // minItems 2
  /** Per-row: one column ("single") or many ("multiple"). */
  selection: "single" | "multiple";
}

export interface RankingConfig {
  choices: Choice[]; // minItems 2
}

/** A contactInfo sub-field (firstName/lastName/email/phone/company). */
export interface ContactSubField {
  show?: boolean;
  required?: boolean;
  placeholder?: I18nStringOptional;
}

/** The five optional contactInfo sub-fields (at least one must be present). */
export type ContactFieldKey = "firstName" | "lastName" | "email" | "phone" | "company";

export interface ContactInfoConfig {
  fields: Partial<Record<ContactFieldKey, ContactSubField>>;
}

/** pictureChoice config: image options + single/multi select toggle. */
export interface PictureChoiceConfig {
  options: PictureChoiceOption[]; // minItems 2
  /** When true, multiple images may be selected; answer is [choiceId]. Default false (single). */
  allowMulti?: boolean;
}

/** An address sub-field (addressLine1/.../country). Same shape as ContactSubField. */
export interface AddressSubField {
  show?: boolean;
  required?: boolean;
  placeholder?: I18nStringOptional;
}

/** The six optional address sub-fields (at least one must be present). */
export type AddressFieldKey =
  | "addressLine1"
  | "addressLine2"
  | "city"
  | "state"
  | "zip"
  | "country";

export interface AddressConfig {
  fields: Partial<Record<AddressFieldKey, AddressSubField>>;
}

/** scheduleMeeting config: a cal.com path ('username' or 'username/event-type'). */
export interface ScheduleMeetingConfig {
  calLink: string;
}

// --- logic --- //
export type Operator =
  | "equals"
  | "notEquals"
  | "contains"
  | "doesNotContain"
  | "startsWith"
  | "endsWith"
  | "greaterThan"
  | "greaterThanOrEqual"
  | "lessThan"
  | "lessThanOrEqual"
  | "before"
  | "after"
  | "isOneOf"
  | "includesAll"
  | "includesAny"
  | "includesOnly"
  | "doesNotInclude"
  | "isAccepted"
  | "isNotAccepted"
  | "isClicked"
  | "isSubmitted"
  | "isSkipped"
  | "isEmpty"
  | "isNotEmpty";

export interface ConditionLeaf {
  leftQuestionId: string;
  operator: Operator;
  value?: string | number | boolean | (string | number)[] | null;
}

export interface ConditionConnector {
  connector: "and" | "or";
  conditions: ConditionGroup[];
}

export type ConditionGroup = ConditionLeaf | ConditionConnector;

/**
 * The right-hand operand of a `calculate` action (survey-schema.json #/$defs/calcValue):
 * a typed LITERAL scalar, or a REF to a question answer / another variable / hidden field by
 * id (all share the id namespace). The tagged shape removes the literal-vs-id ambiguity.
 */
export type CalcValue =
  | { type: "literal"; value: string | number }
  | { type: "ref"; ref: string };

export type Action =
  | { type: "jumpTo"; target: string }
  | { type: "skip"; target: string | string[] }
  | { type: "endSurvey"; endingId?: string }
  | { type: "require"; target: string }
  | {
      /**
       * Mutate a survey variable (Formbricks parity). NON-navigation (like `require`): it does
       * not decide the next step. `operation` must be valid for the variable's type
       * (number -> assign/add/subtract/multiply/divide; text -> assign/concat). A type-
       * incompatible op / unresolvable operand / divide-by-zero is a deterministic no-op.
       */
      type: "calculate";
      variableId: string;
      operation: "assign" | "add" | "subtract" | "multiply" | "divide" | "concat";
      value: CalcValue;
    };

export interface LogicRule {
  id: string;
  condition: ConditionGroup;
  action: Action;
}

// --------------------------------------------------------------------------- //
//  Follow-ups (survey-schema.json #/$defs/followUp)                          //
// --------------------------------------------------------------------------- //
/**
 * A follow-up's trigger: v1 fires on response finish, optionally gated by a condition
 * (a ConditionGroup over the final answers, same shape as logic conditions).
 */
export interface FollowUpTrigger {
  type: "responseFinished";
  /** null/absent = unconditional (fire on every finish). */
  condition?: ConditionGroup | null;
}

/**
 * Who receives a follow-up email — a DISCRIMINATED object (not a string overload, so a
 * fixed email can't collide with a question id). survey-schema.json #/$defs/followUpRecipient.
 */
export type FollowUpRecipient =
  | { type: "contact" }
  | { type: "fixedEmail"; email: string }
  | { type: "responseField"; questionId: string };

/** A follow-up's action: v1 sends an email (i18n subject/body, recall tokens). */
export interface FollowUpAction {
  type: "sendEmail";
  recipient: FollowUpRecipient;
  subject: I18nString;
  body: I18nString;
}

/** A survey follow-up automation (the editor's 4th tab). survey-schema.json #/$defs/followUp. */
export interface FollowUp {
  id: string;
  name?: string;
  trigger: FollowUpTrigger;
  action: FollowUpAction;
}

// --------------------------------------------------------------------------- //
//  Blocks (survey-schema.json #/$defs/block) — additive grouping overlay      //
// --------------------------------------------------------------------------- //
/** Within-block presentation-randomization mode (mirrors the per-question shuffle enum). */
export type BlockRandomize = "none" | "all" | "exceptLast";

/**
 * One question-grouping block (survey `blocks[]`). ADDITIVE + backward-compatible: an
 * absent/empty `blocks` array == today's exact FLAT behavior. A block GROUPS a subset of the
 * flat `questions[]` for display and OPTIONALLY randomizes the order its member questions are
 * PRESENTED at runtime (seeded per-response so back-nav is stable). It does NOT own the
 * questions (they live in `questions[]`) and does NOT reorder the flat array — `questionIds`
 * is a list of REFERENCES into `questions[]`. DEFERRED: block-level logic. survey-schema.json
 * #/$defs/block.
 */
export interface Block {
  id: string;
  title?: string;
  /** Member question ids, in builder display order. Each must be a real question id; a
   * question id may appear in at most one block (enforced by validate_blocks on save). */
  questionIds: string[];
  /** Within-block presentation randomization (default "none" == flat order). */
  randomize?: BlockRandomize;
}

// --------------------------------------------------------------------------- //
//  Variables (survey-schema.json #/$defs/variable) — additive                 //
// --------------------------------------------------------------------------- //
/**
 * An author-defined survey variable (Formbricks parity). A named, typed mutable value seeded
 * from `initialValue`; mutated by `calculate` logic actions as the respondent progresses;
 * readable by recall tokens (#recall:<id>/...#) and logic conditions (leftQuestionId: <id>).
 * `id` shares the question/ending/hidden-field id namespace (it must be unique across it).
 * Variables are NOT persisted into Response.data — their live values are recomputed at runtime
 * by replaying the matching calculate actions (computeVariables + its Python twin).
 */
export interface Variable {
  id: string;
  name: string;
  type: "number" | "text";
  initialValue: number | string;
}

// --------------------------------------------------------------------------- //
//  Quotas (survey-schema.json #/$defs/quota) — additive, server-enforced      //
// --------------------------------------------------------------------------- //
/**
 * One response quota (survey `quotas[]`). Caps how many FINISHED responses matching `condition`
 * (a ConditionGroup — the SAME shape logic rules + follow-ups use) are accepted; the (limit+1)th
 * matching respondent is over-quota and routed to `endingId` (or a built-in 'quota full' screen
 * when absent). ADDITIVE + backward-compatible: an absent/empty `quotas` array == no enforcement.
 * Enforcement is SERVER-ONLY (apps.responses finalize) — the public runtime serializer omits
 * `quotas`, so respondents never see quota conditions. `name` is internal (no i18n).
 */
export interface Quota {
  id: string;
  name: string;
  /** Max FINISHED responses matching `condition` to accept (integer >= 1). */
  limit: number;
  condition: ConditionGroup;
  /** Optional over-quota ending id; absent -> built-in 'quota full' screen. */
  endingId?: string;
}

/**
 * A survey question. Common fields + an open `config` (per-type shapes above).
 * The schema leaves the question object open (additionalProperties omitted) for
 * forward-compat, so `config` is typed loosely and narrowed per renderer.
 */
export interface Question {
  id: string;
  type: QuestionType;
  headline: I18nString;
  subheader?: I18nStringOptional;
  /**
   * Optional RICH-TEXT description (sanitized HTML subset). When present + non-empty
   * for the active language it is rendered (sanitized) BELOW the headline IN PLACE OF
   * the plain `subheader`. Backward-compatible: questions without it fall back to the
   * plain-text `subheader` exactly as before. Stored as an i18nString-optional map so
   * descriptions are per-language; may embed recall tokens (resolved before sanitizing).
   * The schema leaves the question object open (additionalProperties omitted), so this
   * is a forward-compatible addition — see contracts/survey-schema.json #/$defs/question.
   */
  descriptionHtml?: I18nStringOptional;
  required?: boolean;
  logic?: LogicRule[];
  buttonLabel?: I18nStringOptional;
  backButtonLabel?: I18nStringOptional;
  config?: Json;
  [k: string]: Json;
}

// --------------------------------------------------------------------------- //
//  Survey definition (PublicSurvey runtime shape)                            //
// --------------------------------------------------------------------------- //
/**
 * The published survey definition the runtime renders (openapi.yaml PublicSurvey).
 * `endings`/`welcomeCard`/`styling` are optional; the runtime applies defaults.
 */
export interface Survey {
  id?: string;
  name?: string;
  questions: Question[];
  languages: Language[];
  styling?: Styling;
  welcomeCard?: WelcomeCard | null;
  endings?: Ending[];
  /** Survey-level hidden fields captured from the URL at runtime (recall + logic source). */
  hiddenFields?: HiddenField[];
  /** Survey follow-up automations (the editor's 4th tab). Admin/builder shape; the public
   * runtime serializer omits it. snake_case `follow_ups` on the API; camelCase here. */
  followUps?: FollowUp[];
  /** Question-grouping overlay (the editor's "Block 1..N" grouping). ADDITIVE + optional —
   * absent/empty == today's exact FLAT behavior. Exposed on the PUBLIC runtime shape too
   * (the runtime needs it to apply within-block presentation randomization). Same key name
   * on the wire as the column (`blocks`, no snake/camel rename). */
  blocks?: Block[];
  /** Author-defined survey variables (the editor's Variables panel). ADDITIVE + optional —
   * absent/empty == today's exact behavior (no variable replay). Exposed on the PUBLIC runtime
   * shape too (the runtime needs the initial values + types to seed + thread variable state).
   * Same key on the wire as the column (`variables`, no snake/camel rename). */
  variables?: Variable[];
  /** Response quotas (the editor's Settings-tab Quotas section). ADDITIVE + optional —
   * absent/empty == today's exact behavior (no quota enforcement). SERVER-ONLY enforcement: the
   * PUBLIC runtime serializer OMITS this (quota conditions are never leaked to respondents); the
   * runtime learns it is over-quota from the finalize handle (over_quota + quota_full_ending_id),
   * not from this field. Present on the ADMIN/builder shape. Same key on the wire as the column
   * (`quotas`, no snake/camel rename). */
  quotas?: Quota[];
  single_use?: boolean;
  /** Auto-close rules (admin/builder only; the public runtime serializer omits it). null = none. */
  close_on?: CloseConditions | null;
  /** When true, the public response path stores meta.ip; off by default (privacy). */
  capture_ip?: boolean;
  /**
   * Distribution channel (FEATURE_PARITY WS-11). `"link"` (default) = a shareable-URL survey;
   * `"app"` = an in-product Website/App survey shown by the JS widget when a tracked action fires.
   * snake_case on the wire (`survey_type`), matching `single_use`/`capture_ip`. Left UNDEFINED
   * for link surveys so they round-trip byte-identical (the server defaults to "link"). */
  survey_type?: "link" | "app";
  /**
   * ActionClass id STRINGS that TRIGGER this app survey (only meaningful when survey_type==="app").
   * Stored verbatim as a JSON list of ids; the server validates they resolve to real ActionClasses
   * in the survey's environment. Left UNDEFINED for link surveys (no triggers). */
  triggers?: string[];
  /**
   * App-survey display controls (only meaningful when survey_type==="app"). `displayLimit` caps how
   * many times a contact sees the survey; `displayPercentage` (0-100) randomly shows it to that
   * share of eligible views. Opaque to the runtime here; validated server-side. Left UNDEFINED for
   * link surveys. */
  display_options?: { displayLimit?: number; displayPercentage?: number };
}

// --------------------------------------------------------------------------- //
//  Answers (survey-schema.json #/$defs/answerShapes)                         //
// --------------------------------------------------------------------------- //
/** A singleSelect "Other" answer carries the typed value alongside the choice id. */
export interface OtherChoiceAnswer {
  choiceId: string;
  other: string;
}

/**
 * The value stored in Response.data[questionId] per type:
 *   openText/hidden -> string; cta/consent -> boolean; singleSelect -> choiceId
 *   (or {choiceId, other}); multiSelect/ranking/fileUpload -> [choiceId|url];
 *   pictureChoice -> choiceId (single) | [choiceId] (allowMulti); rating -> 1..range;
 *   nps -> 0..10; number -> number; date -> 'YYYY-MM-DD';
 *   matrix -> {rowId: columnId|[columnId]};
 *   contactInfo -> {firstName?,lastName?,email?,phone?,company?};
 *   address -> {addressLine1?,addressLine2?,city?,state?,zip?,country?};
 *   scheduleMeeting -> {booked:true, uid?:string}.
 */
export type AnswerValue =
  | string
  | number
  | boolean
  | string[]
  | OtherChoiceAnswer
  | Record<string, Json>
  | null;

/** Map of questionId -> answer value (Response.data). */
export type AnswerMap = Record<string, AnswerValue>;

// --------------------------------------------------------------------------- //
//  NextStep (the logic evaluator return shape — README §2)                   //
// --------------------------------------------------------------------------- //
export type NextStep =
  | { type: "question"; id: string }
  | { type: "ending"; id: string }
  | { type: "end" };

// --------------------------------------------------------------------------- //
//  Public runtime API shapes (openapi.yaml)                                  //
// --------------------------------------------------------------------------- //
/** Returned by POST .../responses and PATCH/finalize. */
export interface PublicResponseHandle {
  id: string;
  finished: boolean;
  edit_token: string;
  expires_at?: string;
  /**
   * QUOTAS (additive): set by the finalize endpoint. When true, this finished response was the
   * (limit+1)th matching one for some survey quota — the runtime OVERRIDES the ending it would
   * otherwise show. Absent/false on create + patch (not finished) and on a finalize that hit no
   * full quota (byte-identical to the pre-quota handle for surveys with no quotas).
   */
  over_quota?: boolean;
  /**
   * The over-quota ending to route to (a real survey ending id), or null to show the built-in
   * 'quota full' screen. Only meaningful when `over_quota` is true.
   */
  quota_full_ending_id?: string | null;
}
