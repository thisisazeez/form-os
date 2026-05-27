import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["run_ts.ts"],
    // The fixtures are JSON loaded at runtime via fs; no special transform needed.
    environment: "node",
  },
});
