// ============================================================================
// Test helper — install a real in-memory Web Storage on the jsdom globals.
//
// This project's jsdom environment exposes `localStorage`/`sessionStorage` as bare
// `{}` (Web Storage is not wired up), so WidgetStorage's defensive probe treats it
// as "blocked" and falls back to in-memory — which would make the cross-instance
// persistence assertions meaningless. Installing a spec-shaped polyfill lets the
// tests exercise the REAL persistence path (write here, read in a fresh instance).
//
// Imported (for its side effect) by the storage-dependent widget tests only; the
// shared src/test/setup.ts is left untouched so no other suite is affected.
// ============================================================================

class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length(): number {
    return this.map.size;
  }
  clear(): void {
    this.map.clear();
  }
  getItem(key: string): string | null {
    return this.map.has(key) ? (this.map.get(key) as string) : null;
  }
  key(index: number): string | null {
    return Array.from(this.map.keys())[index] ?? null;
  }
  removeItem(key: string): void {
    this.map.delete(key);
  }
  setItem(key: string, value: string): void {
    this.map.set(key, String(value));
  }
}

function ensure(name: "localStorage" | "sessionStorage"): void {
  const current = (globalThis as Record<string, unknown>)[name] as Storage | undefined;
  if (!current || typeof current.clear !== "function") {
    const store = new MemoryStorage();
    Object.defineProperty(globalThis, name, { value: store, configurable: true, writable: true });
    if (typeof window !== "undefined") {
      Object.defineProperty(window, name, { value: store, configurable: true, writable: true });
    }
  }
}

ensure("localStorage");
ensure("sessionStorage");
