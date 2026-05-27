// ============================================================================
// XSS hardening — the consent question renders author HTML (config.html) via
// dangerouslySetInnerHTML. This proves it routes through lib/sanitizeHtml:
// script/event-handler/javascript: payloads are neutralized, while legitimate
// formatting (bold/links) still renders. See lib/sanitizeHtml.ts.
// ============================================================================
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConsentConfig, Question } from "../types";
import { Consent } from "./Consent";

const Q: Question = {
  id: "consent1",
  type: "consent",
  headline: { default: "Please confirm" },
  config: { label: { default: "I agree" } },
} as unknown as Question;

function renderConsent(html: string) {
  const config: ConsentConfig = { label: { default: "I agree" }, html: { default: html } };
  const { container } = render(
    <Consent
      question={Q}
      config={config}
      value={undefined}
      onChange={vi.fn()}
      lang="en"
      defaultLang="en"
      inputId="consent1"
    />,
  );
  return container;
}

describe("Consent — author HTML is sanitized (XSS boundary)", () => {
  it("strips <script> from config.html", () => {
    const c = renderConsent('Terms<script>window.__pwned = true</script>');
    expect(c.querySelector("script")).toBeNull();
    expect(c.innerHTML).not.toContain("__pwned");
    expect(c.textContent).toContain("Terms");
  });

  it("strips inline onerror handlers", () => {
    const c = renderConsent('<img src=x onerror="window.__pwned=1">terms');
    expect(c.innerHTML).not.toContain("onerror");
    expect(c.innerHTML).not.toContain("__pwned");
  });

  it("strips javascript: hrefs but keeps link text", () => {
    const c = renderConsent('<a href="javascript:alert(1)">policy</a>');
    expect(c.innerHTML).not.toContain("javascript:");
    const a = c.querySelector("a");
    if (a) expect(a.getAttribute("href") ?? "").not.toContain("javascript:");
    expect(c.textContent).toContain("policy");
  });

  it("keeps legitimate formatting and hardens safe links", () => {
    const c = renderConsent('Read the <a href="https://example.com/terms">terms</a> <em>carefully</em>');
    expect(c.querySelector("em")?.textContent).toBe("carefully");
    const a = c.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.com/terms");
    expect(a?.getAttribute("rel")).toContain("noopener");
  });
});
