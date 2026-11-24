// Generated from brand/tokens.json by scripts/generate_brand.py — do not edit.
// Run `npm --prefix frontend run brand:generate` after changing the tokens.
export const BRAND = {
  "name": "onForm",
  "wordmark": {
    "prefix": "on",
    "accent": "Form"
  },
  "tagline": "Forms you own. No seat limits, no paywalled logic.",
  "description": "Self-hosted forms, surveys and feedback for teams that own their data.",
  "colors": {
    "brand": "#5B4BE1",
    "brand-hover": "#4838C4",
    "secondary": "#0E7490",
    "success": "#16A34A",
    "ink": "#14161F",
    "muted": "#667085",
    "surface": "#FAFAFC",
    "panel": "#F1F2F7",
    "hairline": "rgba(20,22,31,0.10)"
  },
  "fonts": {
    "display": [
      "Plus Jakarta Sans",
      "sans-serif"
    ],
    "body": [
      "Inter",
      "sans-serif"
    ]
  },
  "links": {
    "repository": "https://github.com/onform/onform",
    "discussions": "https://github.com/onform/onform/discussions",
    "docs": "https://github.com/onform/onform/tree/main/docs"
  },
  "attribution": "Built on an MIT-licensed survey platform by Mohit Badwal."
} as const;

export type BrandColor = keyof typeof BRAND.colors;
