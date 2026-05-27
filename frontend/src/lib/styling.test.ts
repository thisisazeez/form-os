// ============================================================================
// Unit tests for the shared theming mappers (lib/styling). These prove the
// Styling -> CSS mapping; SurveyRuntime.test.tsx proves it's actually wired into
// the runtime, and PreviewPane reuses the SAME functions (preview == production).
// ============================================================================
import { describe, expect, it } from "vitest";

import {
  cardArrangementStyle,
  cardStyle,
  ctaButtonStyle,
  pageBackgroundStyle,
  primaryButtonStyle,
  progressBarColor,
  questionTextStyle,
} from "./styling";
import type { Styling } from "./types";

describe("questionTextStyle", () => {
  it("is empty when no questionColor is set (token utility applies)", () => {
    expect(questionTextStyle({})).toEqual({});
  });
  it("maps questionColor to color", () => {
    expect(questionTextStyle({ questionColor: "#123456" })).toEqual({ color: "#123456" });
  });
});

describe("cardStyle", () => {
  it("is empty for empty styling", () => {
    expect(cardStyle({})).toEqual({});
  });
  it("maps roundness (including 0) to a px borderRadius", () => {
    expect(cardStyle({ roundness: 12 })).toEqual({ borderRadius: "12px" });
    expect(cardStyle({ roundness: 0 })).toEqual({ borderRadius: "0px" });
  });
  it("maps backgroundColor to the card background", () => {
    expect(cardStyle({ backgroundColor: "#fff" })).toEqual({ backgroundColor: "#fff" });
  });
});

describe("primaryButtonStyle", () => {
  it("is empty when no colors are set", () => {
    expect(primaryButtonStyle({})).toEqual({});
  });
  it("prefers buttonColor over brandColor", () => {
    expect(primaryButtonStyle({ brandColor: "#aaa", buttonColor: "#bbb" })).toEqual({
      backgroundColor: "#bbb",
    });
  });
  it("falls back to brandColor and applies buttonTextColor", () => {
    expect(primaryButtonStyle({ brandColor: "#aaa", buttonTextColor: "#000" })).toEqual({
      backgroundColor: "#aaa",
      color: "#000",
    });
  });
});

describe("ctaButtonStyle", () => {
  it("is empty without colors, else uses buttonColor/brandColor for the background only", () => {
    expect(ctaButtonStyle({})).toEqual({});
    expect(ctaButtonStyle({ brandColor: "#aaa" })).toEqual({ backgroundColor: "#aaa" });
    expect(ctaButtonStyle({ brandColor: "#aaa", buttonColor: "#bbb" })).toEqual({
      backgroundColor: "#bbb",
    });
  });
});

describe("progressBarColor", () => {
  it("returns the brand color or undefined", () => {
    expect(progressBarColor({})).toBeUndefined();
    expect(progressBarColor({ brandColor: "#0d9488" })).toBe("#0d9488");
  });
});

describe("pageBackgroundStyle", () => {
  it("is empty for none / missing / valueless backgrounds", () => {
    expect(pageBackgroundStyle({})).toEqual({});
    expect(pageBackgroundStyle({ background: { type: "none" } })).toEqual({});
    expect(pageBackgroundStyle({ background: { type: "color" } })).toEqual({});
  });
  it("maps a color background to backgroundColor", () => {
    expect(pageBackgroundStyle({ background: { type: "color", value: "#101010" } })).toEqual({
      backgroundColor: "#101010",
    });
  });
  it("maps an image background to a cover backgroundImage", () => {
    const style = pageBackgroundStyle({ background: { type: "image", value: "https://x/bg.jpg" } });
    expect(style.backgroundImage).toBe('url("https://x/bg.jpg")');
    expect(style.backgroundSize).toBe("cover");
  });
});

describe("cardArrangementStyle", () => {
  it("tilts the card for 'casual' and is empty for 'straight'/default", () => {
    expect(cardArrangementStyle({ cardArrangement: "casual" })).toEqual({ transform: "rotate(-1.5deg)" });
    expect(cardArrangementStyle({ cardArrangement: "straight" })).toEqual({});
    expect(cardArrangementStyle({} as Styling)).toEqual({});
  });
});
