# onForm — Stitch designs (complete set)

UI mockups generated with **Google Stitch**, branded with the **onForm** visual identity
(teal palette - see ../../STYLE_GUIDE.md). Full per-page prompts (and exact brand tokens) live in
`../../PROMPTS.md`.

## Stitch project
- **Project:** `onForm` — `projects/14794828435381141346`
- **Design system:** `assets/18240071344380900389` ("onForm Brand" — primary `#0d9488`,
  secondary `#3d63dc`, success `#8ac449`, neutral `#faf8f7`, Outfit + Inter, 8px radius)

Each screen has a `.png` (rendered mockup) + `.html` (generated markup — Tailwind via the
`cdn.tailwindcss.com` Play CDN + Outfit/Inter/Material-Symbols from Google Fonts; **open the `.html`
directly in a browser** to see it live). All 25 page concepts from PROMPTS.md are covered; the public
runtime + endings expanded into several mobile screens.

## Admin (desktop)
| File | Screen | Stitch ID |
|------|--------|-----------|
| `dashboard-home.*` | Dashboard home | `0d9d2273b33c4264a95ca348324f9420` |
| `dashboard-org-switcher.*` | Dashboard — sidebar **organization switcher** open (multi-org list + roles + Create org / New workspace; added 2026-05-26) | `c2d7c69814fb4e89a719edbf4a96bdcf` |
| `dashboard-workspace-switcher.*` | Dashboard — sidebar **workspace switcher** open (Production/Development env toggle + workspace list with access pills + New workspace / Workspace settings; added 2026-05-26) | `35f098ce4b714b1092f1cfb56ca39ede` |
| `dashboard-combined-switcher.*` | Dashboard — **unified org+workspace switcher** (drill-in: current org + "Switch ›" to the org list, then the workspace list with access pills; no env toggle). The CHOSEN design to build. Added 2026-05-26 | `826f876c67f64e808067e59a915d5ab0` |
| `surveys-list.*` | Surveys list (+ New survey) | `bf647576cd104afdb3a08146f67e9ea8` |
| `survey-builder.*` | Survey builder (3-pane, **fixed**) | `59d1de4e536e4ecbaf3c6e5f02bfc429` |
| `builder-logic.*` | Survey builder — Logic tab (+ flow map) | `64b4f1d21e194e69b11bc6c912d95b8b` |
| `results-analytics.*` | Survey results & analytics | `f240a426890140c5bf13a0faf6f83d82` |
| `responses-table.*` | Responses table | `787cd7e4e47d45629f191ecdd7586ca1` |
| `response-detail.*` | Single response detail (drawer) | `5a493c2f0fbc48089edc5c60a697aace` |
| `survey-share.*` | Survey share / distribution | `3190b261d7694b1786fd72b308fc1974` |
| `contacts-list.*` | Contacts list | `7b5ffcaba7a045449c511bdb1ea60579` |
| `contact-profile.*` | Contact profile | `b6471a19e8eb45429d80e4529b78b4db` |
| `segments-builder.*` | Segment builder | `989d126cbadf432a99efccbb6cc81e80` |
| `team-members.*` | Team members (+ invite modal) | `acf032582d7e43c99ff0bc493ac8d94b` |
| `teams-access.*` | Teams & access control (RBAC matrix) | `c820a05f9ec2484e94918e4b7e7eefc0` |
| `workspace-settings.*` | Workspace settings | `26da77026c054949bfdd9b73b09ffd96` |
| `org-billing.*` | Org settings & billing | `d9ce2dda7a1042568387596a5ca8b104` |
| `account-settings.*` | Account settings | `e2518ba9819646dda1870101297da3fd` |

## Auth (desktop, responsive)
| File | Screen | Stitch ID |
|------|--------|-----------|
| `login.*` | Login | `89bd186bed4947c28ef12a6dcbd3f235` |
| `signup.*` | Sign up | `6f52e6e29c964eab967ecc96271da869` |
| `reset-password.*` | Forgot / reset password | `57f25355b39e40109f0e9d02e207e21c` |
| `verify-email.*` | Verify email | `baa9d1e480874acab2643f03049d5b3a` |
| `accept-invitation.*` | Accept invitation | `e893fec2ecd24b6d90ceb428e79016c2` |

## Marketing (desktop)
| File | Screen | Stitch ID |
|------|--------|-----------|
| `marketing-landing.*` | Marketing landing page | `6f5c92f4a05348769196a36ab4c849c1` |

## Public survey runtime (mobile)
| File | Screen | Stitch ID |
|------|--------|-----------|
| `public-nps.*` | Runtime — NPS question | `8058bde5ce534887bfd87bd27bc8161f` |
| `public-radio.*` | Runtime — single-select radio cards | `352ce19dd9384d7584f0a3555c85240b` |
| `public-text.*` | Runtime — short-text input | `38299055d7d54666a3717f5c5014e8e6` |
| `public-rating.*` | Runtime — star rating | `e8af20e2500543ab9a1d000d69486145` |
| `survey-completed.*` | Ending — thank-you / completed | `d39c824253804a5cbcaebacb5b6532d0` |
| `link-expired.*` | Survey closed / unavailable | `8c03101fb7854ed2886844e9a7a9c1d8` |

## Reference / superseded
| File | Note |
|------|------|
| `survey-builder.v1-broken.*` | Builder's original collapsed 3-pane render (`e9e03791…`); superseded by `survey-builder.*`. |

## Notes
- **Duplicates in the Stitch project** (safe to delete in the Stitch UI): the broken builder
  (`e9e03791…`), a second Marketing Landing copy (`0f4dbb38…`), and Stitch's auto-generated helper
  assets — a hero product-shot image (`37cdf8ed…`), an envelope illustration (`f662300c…`), and a
  `onForm Logo` SVG (`0c17cc41…`).
- **Font caveat:** Stitch uses **Outfit** (closest match to onForm's **Poppins**, which isn't in
  Stitch's font list). Swap to Poppins in the real front-end build.
- **Animations:** Stitch renders static screens. Intended motion per page is documented in prose in
  `../../PROMPTS.md`.
- Image↔code pairing was verified by matching each `.html` `<title>` to its screen.
