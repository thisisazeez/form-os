// ============================================================================
// Tests for the shared HTML sanitizer (lib/sanitizeHtml.ts).
//
// This is a SECURITY boundary for author-formatted rich-text descriptions, so
// the XSS cases the feature spec calls out are exercised explicitly:
//   - <script> stripped
//   - onerror=/event handlers stripped
//   - javascript: href stripped
//   - the safe subset (b/strong/i/em/u/a[href]/br/p/ul/ol/li/span) kept.
// ============================================================================
import { describe, expect, it } from "vitest";

import { hasRichText, sanitizeHtml } from "./sanitizeHtml";

describe("sanitizeHtml — XSS neutralization", () => {
  it("strips <script> tags entirely (tag + contents)", () => {
    const out = sanitizeHtml('Hello<script>alert("xss")</script> world');
    expect(out).not.toContain("<script");
    expect(out).not.toContain("alert");
    expect(out).toContain("Hello");
    expect(out).toContain("world");
  });

  it("strips inline event handlers (onerror=)", () => {
    const out = sanitizeHtml('<img src=x onerror=alert(1)>caption');
    expect(out.toLowerCase()).not.toContain("onerror");
    expect(out.toLowerCase()).not.toContain("<img");
    expect(out).toContain("caption");
  });

  it("strips onclick / onmouseover handlers on allowed tags", () => {
    const out = sanitizeHtml('<span onclick="steal()">click</span>');
    expect(out.toLowerCase()).not.toContain("onclick");
    expect(out.toLowerCase()).not.toContain("steal");
    expect(out).toContain("click");
  });

  it("strips javascript: hrefs but keeps the link text", () => {
    const out = sanitizeHtml('<a href="javascript:alert(1)">go</a>');
    expect(out.toLowerCase()).not.toContain("javascript:");
    expect(out).toContain("go");
  });

  it("strips data: and vbscript: hrefs", () => {
    const dataUri = sanitizeHtml('<a href="data:text/html,<script>alert(1)</script>">x</a>');
    expect(dataUri.toLowerCase()).not.toContain("data:text/html");
    const vb = sanitizeHtml('<a href="vbscript:msgbox(1)">y</a>');
    expect(vb.toLowerCase()).not.toContain("vbscript:");
  });

  it("removes disallowed structural tags (iframe/style/object/svg)", () => {
    expect(sanitizeHtml('<iframe src="evil"></iframe>').toLowerCase()).not.toContain("<iframe");
    expect(sanitizeHtml('<style>body{display:none}</style>').toLowerCase()).not.toContain("<style");
    expect(sanitizeHtml('<object data="evil"></object>').toLowerCase()).not.toContain("<object");
    const svg = sanitizeHtml('<svg><script>alert(1)</script></svg>');
    expect(svg.toLowerCase()).not.toContain("<svg");
    expect(svg.toLowerCase()).not.toContain("<script");
  });

  it("neutralizes a nested/obfuscated script payload", () => {
    const out = sanitizeHtml('<p>ok</p><scr<script>ipt>alert(1)</scr</script>ipt>');
    // No executable <script> ELEMENT survives — the angle brackets are escaped
    // to &lt;/&gt; so any leftover "alert(1)" is inert text, never parsed as code.
    expect(out.toLowerCase()).not.toContain("<script");
    expect(out).not.toContain("<scr");
    expect(out).toContain("ok");
  });
});

describe("sanitizeHtml — safe subset kept", () => {
  it("keeps bold/italic/underline/emphasis tags", () => {
    expect(sanitizeHtml("<b>b</b>")).toContain("<b>b</b>");
    expect(sanitizeHtml("<strong>s</strong>")).toContain("<strong>s</strong>");
    expect(sanitizeHtml("<i>i</i>")).toContain("<i>i</i>");
    expect(sanitizeHtml("<em>e</em>")).toContain("<em>e</em>");
    expect(sanitizeHtml("<u>u</u>")).toContain("<u>u</u>");
  });

  it("keeps paragraphs, line breaks, lists and spans", () => {
    expect(sanitizeHtml("<p>para</p>")).toContain("<p>para</p>");
    expect(sanitizeHtml("line<br>break")).toMatch(/line<br\s*\/?>break/);
    const list = sanitizeHtml("<ul><li>a</li><li>b</li></ul>");
    expect(list).toContain("<ul>");
    expect(list).toContain("<li>a</li>");
    expect(sanitizeHtml('<span>txt</span>')).toContain("<span>txt</span>");
  });

  it("keeps a safe http(s) link and hardens it with target+rel", () => {
    const out = sanitizeHtml('<a href="https://example.com">site</a>');
    expect(out).toContain('href="https://example.com"');
    expect(out).toContain('target="_blank"');
    expect(out).toContain("noopener");
    expect(out).toContain("noreferrer");
    expect(out).toContain("site");
  });

  it("keeps a mailto link", () => {
    const out = sanitizeHtml('<a href="mailto:a@b.com">mail</a>');
    expect(out).toContain('href="mailto:a@b.com"');
  });

  it("passes plain text through unchanged (no markup)", () => {
    expect(sanitizeHtml("just plain text")).toBe("just plain text");
    expect(sanitizeHtml("")).toBe("");
    expect(sanitizeHtml(undefined)).toBe("");
    expect(sanitizeHtml(null)).toBe("");
  });

  it("escapes naked angle brackets / ampersands in plain text safely", () => {
    // A description that happens to contain '<' or '&' must not become broken markup.
    const out = sanitizeHtml("a < b & c");
    expect(out).not.toContain("<b ");
    expect(out).toContain("a");
    expect(out).toContain("c");
  });
});

describe("hasRichText", () => {
  it("is false for empty / whitespace-only / empty-tag content", () => {
    expect(hasRichText("")).toBe(false);
    expect(hasRichText(undefined)).toBe(false);
    expect(hasRichText(null)).toBe(false);
    expect(hasRichText("<p></p>")).toBe(false);
    expect(hasRichText("<p>   </p>")).toBe(false);
    expect(hasRichText("<br>")).toBe(false);
  });

  it("is true when there is visible text content", () => {
    expect(hasRichText("<p>hello</p>")).toBe(true);
    expect(hasRichText("plain")).toBe(true);
    expect(hasRichText("<b>x</b>")).toBe(true);
  });

  it("is false when only a stripped (unsafe) tag would remain", () => {
    expect(hasRichText("<script>alert(1)</script>")).toBe(false);
  });
});
