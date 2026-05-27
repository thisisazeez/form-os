// ============================================================================
// Widget storage: userId persistence + in-session de-dup.
// ============================================================================
import "./test-storage"; // install in-memory Web Storage on jsdom globals (side effect)

import { beforeEach, describe, expect, it } from "vitest";

import { generateUserId, WidgetStorage } from "./storage";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("WidgetStorage — userId", () => {
  it("generates + persists a userId when none is set", () => {
    const s = new WidgetStorage();
    const id = s.ensureUserId();
    expect(id).toBeTruthy();
    // Persisted across a fresh instance (same localStorage).
    expect(new WidgetStorage().getUserId()).toBe(id);
  });

  it("setUserId overrides + persists the host-supplied id", () => {
    const s = new WidgetStorage();
    s.ensureUserId();
    s.setUserId("crm-42");
    expect(s.getUserId()).toBe("crm-42");
    expect(new WidgetStorage().getUserId()).toBe("crm-42");
  });

  it("reset clears userId and the shown-set", () => {
    const s = new WidgetStorage();
    const first = s.ensureUserId();
    s.markShown("sv-1");
    s.reset();
    expect(localStorage.getItem("openSurvey.userId")).toBeNull();
    const second = s.ensureUserId();
    expect(second).not.toBe(first);
    expect(s.hasShown("sv-1")).toBe(false);
  });
});

describe("WidgetStorage — in-session de-dup", () => {
  it("tracks shown surveys for the session", () => {
    const s = new WidgetStorage();
    expect(s.hasShown("sv-1")).toBe(false);
    s.markShown("sv-1");
    expect(s.hasShown("sv-1")).toBe(true);
    // Survives a new instance reading the same sessionStorage.
    expect(new WidgetStorage().hasShown("sv-1")).toBe(true);
  });
});

describe("generateUserId", () => {
  it("produces unique uuid-shaped ids", () => {
    const a = generateUserId();
    const b = generateUserId();
    expect(a).not.toBe(b);
    expect(a).toMatch(/^[0-9a-f-]{36}$/i);
  });
});
