import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// ============================================================================
// OpenSurvey — Vite config for the React island pipeline (PLAN.md §8).
//
// Two islands are built and mounted into Django templates via django-vite:
//   - builder:  the survey builder (admin, behind auth)   [Wave 3]
//   - runtime:  the public survey runtime (/<org_slug>/s/) [Wave 3]
// Phase-0 ships stub entrypoints that render a placeholder — proving the
// build → manifest → django-vite → template mount pipeline end-to-end.
//
// django-vite reads `dist/.vite/manifest.json` (manifest mode) or proxies to
// the dev server when DJANGO_VITE_DEV_MODE=True. STATIC settings collect `dist`.
// ============================================================================
export default defineConfig({
  plugins: [react()],

  // Assets are served under Django's STATIC_URL. django-vite prepends the static
  // URL itself, so we use a relative base and let Django resolve the prefix.
  base: "/static/",

  build: {
    outDir: resolve(__dirname, "dist"),
    emptyOutDir: true,
    // REQUIRED by django-vite: emit dist/.vite/manifest.json mapping entries → hashed files.
    manifest: true,
    rollupOptions: {
      // One input per island. django-vite references these by their src path.
      // Both islands are TypeScript (Wave 3): the runtime (public survey) and the
      // builder (admin authoring UI). The host templates reference these exact paths
      // via {% vite_asset %}, so the manifest keys must stay in sync with them.
      input: {
        builder: resolve(__dirname, "src/islands/builder/main.tsx"),
        runtime: resolve(__dirname, "src/islands/runtime/main.tsx"),
      },
    },
  },

  server: {
    port: 5173,
    strictPort: true,
  },
});
