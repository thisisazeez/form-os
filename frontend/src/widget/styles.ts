// ============================================================================
// OpenSurvey widget — scoped CSS injected into the shadow root.
//
// The reused runtime (SurveyRuntime + QuestionRenderer) is styled with Tailwind
// UTILITY classes that, on the admin/public pages, resolve against the Tailwind
// CDN config in templates/base.html (which registers the OpenSurvey brand tokens).
// Inside the widget's SHADOW ROOT those host-page styles do NOT cascade in, so we
// ship a curated subset of the utilities the runtime actually uses + a small popup
// chrome. This is deliberately a SUBSET, not a full Tailwind build — exact visual
// parity with the hosted runtime is DEFERRED (documented in README.md).
//
// `:host` chrome positions the survey as a bottom-right popup card with an overlay
// affordance; `.cs-close` is our injected dismiss button (not part of the runtime).
// ============================================================================

/** Brand tokens — mirror templates/base.html tailwind.config (kept in one place). */
const TOKENS = `
  --cs-brand: #0d9488;
  --cs-brand-hover: #c93d12;
  --cs-success: #8ac449;
  --cs-ink: #222b27;
  --cs-muted: #6b7470;
  --cs-surface: #faf8f7;
  --cs-panel: #f1eeec;
  --cs-hairline: #e7e2de;
`;

export const WIDGET_CSS = `
:host {
  ${TOKENS}
  all: initial;
  position: fixed;
  z-index: 2147483000;
  bottom: 20px;
  right: 20px;
  width: min(420px, calc(100vw - 40px));
  max-height: calc(100vh - 40px);
  font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color: var(--cs-ink);
}
.cs-root { all: initial; font-family: inherit; color: var(--cs-ink); }
*, *::before, *::after { box-sizing: border-box; }
.cs-card-wrap {
  position: relative;
  background: #fff;
  border: 1px solid var(--cs-hairline);
  border-radius: 16px;
  box-shadow: 0 10px 40px rgba(0,0,0,.18);
  overflow: hidden;
  max-height: calc(100vh - 40px);
  overflow-y: auto;
}
.cs-close {
  position: absolute; top: 10px; right: 10px;
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  border: none; border-radius: 8px; cursor: pointer;
  background: var(--cs-panel); color: var(--cs-muted);
  font-size: 18px; line-height: 1;
}
.cs-close:hover { background: var(--cs-hairline); color: var(--cs-ink); }

/* ---- runtime utility subset (the classes SurveyRuntime/QuestionRenderer emit) ---- */
.flex { display: flex; }
.inline-flex { display: inline-flex; }
.flex-col { flex-direction: column; }
.items-center { align-items: center; }
.justify-center { justify-content: center; }
.justify-between { justify-content: space-between; }
.text-center { text-align: center; }
.w-full { width: 100%; }
.h-full { height: 100%; }
.min-h-screen { min-height: auto; }      /* popup, not full page */
.max-w-2xl, .max-w-md, .max-w-sm, .max-w-xs, .max-w-none, .max-w-\\[280px\\], .max-w-\\[300px\\] { max-width: 100%; }
.mx-auto { margin-left: auto; margin-right: auto; }
.px-2 { padding-left: .5rem; padding-right: .5rem; }
.px-4 { padding-left: 1rem; padding-right: 1rem; }
.px-6 { padding-left: 1.5rem; padding-right: 1.5rem; }
.py-2 { padding-top: .5rem; padding-bottom: .5rem; }
.py-3\\.5 { padding-top: .875rem; padding-bottom: .875rem; }
.py-8 { padding-top: 1.25rem; padding-bottom: 1.25rem; }
.p-6 { padding: 1.5rem; }
.p-8 { padding: 1.75rem; }
.pt-2 { padding-top: .5rem; }
.mb-5 { margin-bottom: 1.25rem; }
.mb-6 { margin-bottom: 1.5rem; }
.mb-8 { margin-bottom: 1.5rem; }
.mt-3 { margin-top: .75rem; }
.mt-4 { margin-top: 1rem; }
.mt-8 { margin-top: 1.5rem; }
.mt-10 { margin-top: 1.75rem; }
.mt-16 { margin-top: 2rem; }
.space-y-6 > * + * { margin-top: 1.5rem; }
.gap-2 { gap: .5rem; }
.rounded-lg { border-radius: .5rem; }
.rounded-2xl { border-radius: 1rem; }
.rounded-full { border-radius: 9999px; }
.border { border-width: 1px; border-style: solid; }
.border-hairline { border-color: var(--cs-hairline); }
.bg-white { background-color: #fff; }
.bg-brand { background-color: var(--cs-brand); }
.bg-panel { background-color: var(--cs-panel); }
.bg-success\\/15 { background-color: rgba(138,196,73,.15); }
.shadow-sm { box-shadow: 0 1px 2px rgba(0,0,0,.06); }
.shadow-lg { box-shadow: 0 10px 30px rgba(0,0,0,.12); }
.text-ink { color: var(--cs-ink); }
.text-muted { color: var(--cs-muted); }
.text-white { color: #fff; }
.text-red-600 { color: #dc2626; }
.font-display { font-family: Poppins, Inter, system-ui, sans-serif; }
.font-bold { font-weight: 700; }
.font-medium { font-weight: 500; }
.font-semibold { font-weight: 600; }
.tracking-tight { letter-spacing: -.01em; }
.text-sm { font-size: .875rem; }
.text-base { font-size: 1rem; }
.text-2xl { font-size: 1.5rem; }
.text-3xl { font-size: 1.875rem; }
.leading-relaxed { line-height: 1.625; }
.outline-none { outline: none; }
.transition, .transition-colors { transition: all .15s ease; }
.hover\\:bg-brand-hover:hover { background-color: var(--cs-brand-hover); }
.hover\\:text-ink:hover { color: var(--cs-ink); }
.disabled\\:opacity-50:disabled { opacity: .5; }
.object-contain { object-fit: contain; }
.max-h-16 { max-height: 4rem; }
.w-auto { width: auto; }
.h-20 { height: 5rem; } .w-20 { width: 5rem; }
.h-28 { height: 7rem; } .w-28 { width: 7rem; }

/* form controls inherit reasonable defaults inside the isolated root */
.cs-root input[type=text], .cs-root input[type=email], .cs-root input[type=number],
.cs-root input[type=tel], .cs-root input[type=date], .cs-root textarea, .cs-root select {
  font: inherit; color: inherit; width: 100%;
  padding: .55rem .7rem; border: 1px solid var(--cs-hairline);
  border-radius: .5rem; background: #fff;
}
.cs-root button { font: inherit; cursor: pointer; }
.cs-root h1, .cs-root h2, .cs-root p { margin: 0; }
`;
