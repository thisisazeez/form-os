// ============================================================================
// OpenSurvey — shared survey THEMING helpers (pure, framework-light).
//
// Single source of truth for turning a survey's `Styling` object (the frozen
// contracts/survey-schema.json #/$defs/styling shape — see ../lib/types) into
// inline CSS. Consumed by BOTH:
//   - the public runtime (islands/runtime/SurveyRuntime) — what respondents see
//   - the builder live preview (islands/builder/PreviewPane) — what authors see
// so "preview == production" theming is guaranteed by construction (one mapper,
// two call sites). The builder's Styling tab edits the same object.
//
// All keys are optional; an unset key yields NO inline override, letting the
// brand Tailwind token utilities (bg-brand / text-ink / bg-white …) apply. The
// BRAND_DEFAULTS below mirror templates/base.html's tailwind.config tokens and
// are used ONLY to seed the builder's color SWATCHES (so a native <input
// type=color> shows the effective color, not black) — they are never persisted.
// ============================================================================
import type { CSSProperties } from "react";

import type { Styling } from "./types";

/**
 * Effective brand-token defaults (mirror templates/base.html tailwind.config).
 * Used to seed the builder color pickers when a key is unset — NOT written to
 * the survey unless the author actually changes them (omit-when-default).
 */
export const BRAND_DEFAULTS = {
  brandColor: "#0d9488",
  questionColor: "#222b27", // ink
  backgroundColor: "#ffffff", // the card surface (white)
  buttonColor: "#0d9488",
  buttonTextColor: "#ffffff",
  pageBackground: "#faf8f7", // surface (page behind the card)
} as const;

/** Root text color of the in-progress survey (question text). */
export function questionTextStyle(styling: Styling): CSSProperties {
  const style: CSSProperties = {};
  if (styling.questionColor) style.color = styling.questionColor;
  return style;
}

/** The question CARD container — corner roundness + card background color. */
export function cardStyle(styling: Styling): CSSProperties {
  const style: CSSProperties = {};
  if (styling.roundness !== undefined) style.borderRadius = `${styling.roundness}px`;
  if (styling.backgroundColor) style.backgroundColor = styling.backgroundColor;
  return style;
}

/**
 * Primary button: brand token by default (the `bg-brand` utility), overridden by
 * the survey's configured button/brand color + text color when present.
 */
export function primaryButtonStyle(styling: Styling): CSSProperties {
  const style: CSSProperties = {};
  const bg = styling.buttonColor ?? styling.brandColor;
  if (bg) style.backgroundColor = bg;
  if (styling.buttonTextColor) style.color = styling.buttonTextColor;
  return style;
}

/**
 * Secondary CTA link (thank-you screen): background only — defaults to `bg-brand`,
 * overridable by the configured button/brand color. (Text stays the `text-white`
 * utility to match the design; an empty object lets the utility apply.)
 */
export function ctaButtonStyle(styling: Styling): CSSProperties {
  const bg = styling.buttonColor ?? styling.brandColor;
  return bg ? { backgroundColor: bg } : {};
}

/** Progress-bar fill color (the brand accent). Undefined => the `bg-brand` utility. */
export function progressBarColor(styling: Styling): string | undefined {
  return styling.brandColor;
}

/**
 * PAGE background (the area OUTSIDE the card): color / image / none.
 * Empty / "none" / missing value => no override (caller keeps its bg-surface or
 * bg-panel default). This is distinct from `backgroundColor`, which themes the
 * CARD, not the page.
 */
export function pageBackgroundStyle(styling: Styling): CSSProperties {
  const bg = styling.background;
  if (!bg || bg.type === "none" || !bg.value) return {};
  if (bg.type === "image") {
    return {
      backgroundImage: `url("${bg.value}")`,
      backgroundSize: "cover",
      backgroundPosition: "center",
      backgroundRepeat: "no-repeat",
    };
  }
  // type === "color"
  return { backgroundColor: bg.value };
}

/**
 * "Casual" card arrangement: a subtle tilt on the (single) active card. The
 * runtime shows one card at a time, so Formbricks's stacked-deck effect can't
 * translate literally; a gentle rotation is the honest, contained equivalent.
 * "straight" (default) => no transform.
 */
export function cardArrangementStyle(styling: Styling): CSSProperties {
  return styling.cardArrangement === "casual" ? { transform: "rotate(-1.5deg)" } : {};
}
