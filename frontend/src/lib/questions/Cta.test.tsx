// ============================================================================
// XSS hardening — the CTA/statement question renders author HTML (config.html)
// via dangerouslySetInnerHTML. This proves it routes through lib/sanitizeHtml:
// script/event-handler/javascript: payloads are neutralized, while legitimate
// formatting (bold/links) still renders. See lib/sanitizeHtml.ts.
// ============================================================================
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CtaConfig, Question } from "../types";
import { Cta } from "./Cta";

// Cta ignores `question` (it reads `config`), but the prop is required by the
// uniform renderer signature — a minimal valid stub satisfies the type.
const Q: Question = {
  id: "cta1",
  type: "cta",
  headline: { default: "Heads up" },
  config: {},
} as unknown as Question;

function renderCta(html: string) {
  const config: CtaConfig = { html: { default: html }, buttonLabel: { default: "Continue" } };
  const { container } = render(
    <Cta
      question={Q}
      config={config}
      value={undefined}
      onChange={vi.fn()}
      lang="en"
      defaultLang="en"
      inputId="cta1"
    />,
  );
  return container;
}

describe("Cta — author HTML is sanitized (XSS boundary)", () => {
  it("strips <script> from config.html", () => {
    const c = renderCta('Hello<script>window.__pwned = true</script>');
    expect(c.querySelector("script")).toBeNull();
    expect(c.innerHTML).not.toContain("__pwned");
    expect(c.textContent).toContain("Hello");
  });

  it("strips inline onerror handlers", () => {
    const c = renderCta('<img src=x onerror="window.__pwned=1">copy');
    expect(c.innerHTML).not.toContain("onerror");
    expect(c.innerHTML).not.toContain("__pwned");
  });

  it("strips javascript: hrefs but keeps link text", () => {
    const c = renderCta('<a href="javascript:alert(1)">click</a>');
    const a = c.querySelector("a");
    // DOMPurify drops the dangerous href (or the anchor entirely); either way no js: survives.
    expect(c.innerHTML).not.toContain("javascript:");
    if (a) expect(a.getAttribute("href") ?? "").not.toContain("javascript:");
    expect(c.textContent).toContain("click");
  });

  it("keeps legitimate formatting and hardens safe links", () => {
    const c = renderCta('<b>Bold</b> and <a href="https://example.com">link</a>');
    expect(c.querySelector("b")?.textContent).toBe("Bold");
    const a = c.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.com");
    expect(a?.getAttribute("rel")).toContain("noopener");
    expect(a?.getAttribute("target")).toBe("_blank");
  });
});
