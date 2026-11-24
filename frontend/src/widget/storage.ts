// ============================================================================
// onForm widget — userId persistence + in-session de-dup.
//
// userId:   stable per-browser id. If the host calls setUserId(id) we use that and
//           persist it (localStorage). Otherwise we generate + persist an anonymous
//           id once and reuse it across reloads. reset() clears it (a new visitor).
// shown:    surveys already shown THIS SESSION (sessionStorage) so we don't re-show
//           a survey within the same tab session even if its trigger fires again.
//           The SERVER still enforces displayLimit + recontact cooldown across
//           sessions (keyed on the userId-resolved Contact); this is the cheap
//           in-tab guard the task asks for ("don't re-show in-session").
//
// All storage access is defensively wrapped: a host page may block storage (privacy
// mode / sandboxed iframe), in which case we fall back to an in-memory value so the
// widget still works for the lifetime of the page.
// ============================================================================

const USER_ID_KEY = "openSurvey.userId";
const SHOWN_KEY = "openSurvey.shown";

/** RFC4122-ish v4 uuid; uses crypto.randomUUID when available, else a fallback. */
export function generateUserId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  // Fallback for very old/locked-down environments (jsdom without randomUUID, etc).
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function safeGet(storage: Storage | undefined, key: string): string | null {
  try {
    return storage ? storage.getItem(key) : null;
  } catch {
    return null;
  }
}

function safeSet(storage: Storage | undefined, key: string, value: string): void {
  try {
    storage?.setItem(key, value);
  } catch {
    /* storage blocked — in-memory fallback handled by caller */
  }
}

function safeRemove(storage: Storage | undefined, key: string): void {
  try {
    storage?.removeItem(key);
  } catch {
    /* ignore */
  }
}

/**
 * Persistence façade. Holds an in-memory mirror so the widget keeps working even
 * when Web Storage throws (sandboxed/3rd-party-cookie-blocked contexts).
 */
export class WidgetStorage {
  private local: Storage | undefined;
  private session: Storage | undefined;
  private memUserId: string | null = null;
  private memShown: Set<string> | null = null;

  constructor(local?: Storage, session?: Storage) {
    // Default to the real Web Storage; guarded so a throwing accessor never breaks init.
    this.local = local ?? safeWindowStorage("localStorage");
    this.session = session ?? safeWindowStorage("sessionStorage");
  }

  /** Read the persisted userId (memory mirror first, then localStorage). */
  getUserId(): string | null {
    if (this.memUserId) return this.memUserId;
    const stored = safeGet(this.local, USER_ID_KEY);
    if (stored) this.memUserId = stored;
    return this.memUserId;
  }

  /** Persist a userId (host-supplied or generated). */
  setUserId(id: string): void {
    this.memUserId = id;
    safeSet(this.local, USER_ID_KEY, id);
  }

  /** Ensure a userId exists: return the persisted one, or generate+persist a new one. */
  ensureUserId(): string {
    const existing = this.getUserId();
    if (existing) return existing;
    const fresh = generateUserId();
    this.setUserId(fresh);
    return fresh;
  }

  /** True if this survey was already shown in this tab session. */
  hasShown(surveyId: string): boolean {
    return this.readShown().has(surveyId);
  }

  /** Mark a survey as shown for this tab session. */
  markShown(surveyId: string): void {
    const set = this.readShown();
    set.add(surveyId);
    this.memShown = set;
    safeSet(this.session, SHOWN_KEY, JSON.stringify([...set]));
  }

  /** Clear userId + the shown-set (reset() — a brand new visitor). */
  reset(): void {
    this.memUserId = null;
    this.memShown = new Set();
    safeRemove(this.local, USER_ID_KEY);
    safeRemove(this.session, SHOWN_KEY);
  }

  private readShown(): Set<string> {
    if (this.memShown) return this.memShown;
    const raw = safeGet(this.session, SHOWN_KEY);
    let set: Set<string>;
    if (raw) {
      try {
        const arr = JSON.parse(raw) as string[];
        set = new Set(Array.isArray(arr) ? arr : []);
      } catch {
        set = new Set();
      }
    } else {
      set = new Set();
    }
    this.memShown = set;
    return set;
  }
}

/** Access window.localStorage/sessionStorage without throwing if it's blocked/absent. */
function safeWindowStorage(kind: "localStorage" | "sessionStorage"): Storage | undefined {
  try {
    const s = (globalThis as unknown as Record<string, Storage | undefined>)[kind];
    // Touch it to surface a SecurityError early in locked-down contexts.
    if (s) {
      const probe = "__openSurvey_probe__";
      s.setItem(probe, "1");
      s.removeItem(probe);
    }
    return s;
  } catch {
    return undefined;
  }
}
