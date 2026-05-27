// ============================================================================
// OpenSurvey — Builder island local types.
//
// The builder edits the SHARED Survey definition (../../lib/types — the frozen
// contracts/survey-schema.json shape). It does NOT define new survey types; these
// are builder-UI-only types (the palette catalogue, save state, validation surface).
// ============================================================================
import type { QuestionType } from "../../lib/types";

/** The question types this builder can author (matches the runtime's SUPPORTED_TYPES). */
export type CoreQuestionType = Extract<
  QuestionType,
  | "openText"
  | "singleSelect"
  | "multiSelect"
  | "pictureChoice"
  | "rating"
  | "nps"
  | "cta"
  | "consent"
  | "date"
  | "number"
  | "ranking"
  | "matrix"
  | "contactInfo"
  | "address"
  | "scheduleMeeting"
  | "fileUpload"
>;

/**
 * The builder's top-level tabs. "build" = the existing authoring panes (palette /
 * list / editor / live preview); "endings" = the content panes for the welcome card +
 * the ending/thank-you screens (beside the live preview); "style" = the theming editor
 * (colors / background / logo / roundness) beside the live preview; "settings" =
 * survey-level behavior (single-use links / response limit / close date / capture IP);
 * "followups" = the follow-up automations editor (email on response, optionally gated by a
 * condition — sits after Settings, before Logic, matching the target's 4th tab);
 * "blocks" = the question-grouping editor (group questions into blocks + per-block within-block
 * randomization + block reorder — an additive overlay that never reorders the flat questions[]);
 * "logic" = the conditional-branching editor + flow map. The toolbar (Back / status
 * pill / Save / Publish / Discard) is shared across all tabs and stays mounted
 * regardless of tab.
 */
export type BuilderTab =
  | "build"
  | "endings"
  | "style"
  | "settings"
  | "followups"
  | "blocks"
  | "logic";

/**
 * An ActionClass available in the survey's environment (FEATURE_PARITY WS-11). The host page
 * injects these (apps.actions.serializers.ActionClassSerializer shape) so the Settings tab can
 * render the app-survey trigger multi-select with human names; the builder only reads `id` + `name`
 * + `type` (the rest of the row is unused here).
 */
export interface ActionClass {
  id: string;
  name: string;
  type: "code" | "pageview" | "click";
}

/** A palette entry: the type plus its human label + a one-line hint shown in the add menu. */
export interface PaletteEntry {
  type: CoreQuestionType;
  /** Short label, e.g. "Short text". */
  label: string;
  /** One-line description for the palette tile. */
  hint: string;
  /** A compact glyph (emoji) shown on the tile — matches the lightweight admin aesthetic. */
  glyph: string;
}

/** The save button's lifecycle, surfaced in the toolbar. */
export type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; messages: string[] };

/**
 * The Publish button's lifecycle (status transition via POST /surveys/{id}/status),
 * surfaced in the toolbar. `error` messages reuse the ErrorBanner; on success the
 * toolbar reflects the new status pill.
 */
export type PublishState =
  | { kind: "idle" }
  | { kind: "publishing" }
  | { kind: "error"; messages: string[] };
