import react from "@vitejs/plugin-react";
import { resolve } from "path";
import { defineConfig } from "vite";

// ============================================================================
// onForm — Vite config for the EMBEDDABLE WIDGET bundle (FEATURE_PARITY WS-11).
//
// Separate from the django-vite island pipeline (vite.config.js, which builds the
// admin `builder` + public `runtime` islands with hashed names + a manifest). This
// target produces ONE self-contained IIFE file with React bundled IN, at a STABLE,
// unhashed path so the embed <script src> can be a fixed URL:
//
//   frontend/dist/widget/opensurvey-widget.js
//     → collected into STATIC_ROOT/widget/opensurvey-widget.js by collectstatic
//     → served by Django at  /static/widget/opensurvey-widget.js
//
// (frontend/dist is in STATICFILES_DIRS, so the file is a static asset like the
// island bundles — but with NO manifest/hash, because the customer hard-codes the URL.)
//
// emptyOutDir is FALSE so this build does NOT wipe the island bundles (and vice-versa);
// the two targets write to disjoint subtrees of dist/ (dist/assets vs dist/widget).
// Run order is irrelevant; `npm run build` chains both.
// ============================================================================
export default defineConfig({
  plugins: [react()],
  // The widget runs on arbitrary customer pages; production must be optimized + quiet.
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: resolve(__dirname, "dist/widget"),
    // Do NOT clear dist/ — the island pipeline (vite.config.js) writes dist/assets +
    // dist/.vite/manifest.json there. Disjoint output subtrees, so both can coexist.
    emptyOutDir: false,
    // No manifest: the embed URL is hard-coded by the customer; the filename must be stable.
    manifest: false,
    // Inline everything (CSS is injected at runtime into the shadow root, so there should be
    // no separate CSS asset; bump the limit so any tiny chunk stays inlined too).
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    lib: {
      entry: resolve(__dirname, "src/widget/main.ts"),
      name: "OpenSurveyWidget", // global for the IIFE (window.openSurvey is set by the code)
      formats: ["iife"],
      fileName: () => "opensurvey-widget.js",
    },
    rollupOptions: {
      // Bundle React + ReactDOM IN (no externals) — the host page needs nothing.
      output: {
        // Keep the single-file contract: no code-splitting, no extra chunks.
        inlineDynamicImports: true,
      },
    },
  },
});
