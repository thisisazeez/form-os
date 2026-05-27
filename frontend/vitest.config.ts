import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// ============================================================================
// OpenSurvey — Vitest config for the React island unit/flow tests.
//
// jsdom environment + @testing-library/react. Tests live alongside the code
// under src/ (*.test.ts / *.test.tsx). Network is mocked per-test (vi.fn on
// global.fetch); no real API is hit.
// ============================================================================
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
  },
});
