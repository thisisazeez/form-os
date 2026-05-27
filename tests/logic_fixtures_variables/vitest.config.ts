import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["run_ts.ts"],
    environment: "node",
  },
});
