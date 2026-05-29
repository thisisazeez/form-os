# OpenSurvey vs Typeform vs Formbricks

A quick, honest comparison to help you decide whether OpenSurvey fits your needs. If you want
**full ownership of your survey data**, **no per-seat pricing**, and **team/RBAC features without an
enterprise upgrade**, OpenSurvey is built for you. If you want a zero-ops hosted product and don't
mind a subscription, a SaaS tool may suit you better. We'll be straight about the trade-offs.

> OpenSurvey is young and self-hosted. Typeform is a mature commercial SaaS; Formbricks is an
> established open-source experience-management platform. This table reflects general, publicly
> known positioning — always check each vendor's current plans for specifics.

## At a glance

| | **OpenSurvey** | **Typeform** | **Formbricks** |
|---|---|---|---|
| **License** | MIT (fully open source) | Proprietary SaaS | Open-core (some features gated) |
| **Hosting** | Self-hosted (your infra) | Vendor-hosted only | Self-hosted or cloud |
| **Pricing model** | Free — you pay only for your infra | Per-month, response/seat tiers | Free CE; paid for some features/cloud |
| **Your data** | 100% on your servers | On vendor servers | Yours (if self-hosted) |
| **Visual builder** | ✅ | ✅ | ✅ |
| **Conditional logic** | ✅ (visual flow map) | ✅ | ✅ |
| **Analytics** | ✅ funnel, drop-off, NPS, trends | ✅ | ✅ |
| **Contacts & segments** | ✅ typed attributes, PII masking | Limited | ✅ |
| **Teams & RBAC** | ✅ org roles + workspace teams | Paid tiers | Varies by plan |
| **Webhooks / API keys** | ✅ | ✅ (higher tiers) | ✅ |
| **White-label** | ✅ | Paid tiers | Varies |
| **Embeddable widget** | ✅ (link, iframe, JS widget) | ✅ | ✅ |
| **Multi-tenant** | ✅ schema-per-org | n/a | n/a |
| **Self-host effort** | You run Postgres + the app | None | You run the stack |

## When OpenSurvey is the right call

- You don't want customer/respondent data leaving your infrastructure (compliance, privacy, or principle).
- Per-seat or per-response pricing doesn't fit how you work.
- You want **teams, roles, contacts, segments, webhooks, and white-label as first-class, unpaywalled features**.
- You're comfortable running a Postgres + Django/React app (a one-command Docker setup is provided).
- You want to **modify or extend** the platform — it's MIT licensed, so you can do anything.

## When a hosted SaaS might be better

- You want **zero operational overhead** — no servers, upgrades, or backups to manage.
- You need a polished managed product today and a subscription is acceptable.
- You rely on a large library of native integrations/templates a vendor maintains for you.

## Why OpenSurvey exists

Many survey tools gate the genuinely useful capabilities — team access control, contacts/segments,
the full API, white-labeling — behind higher paid tiers or an enterprise license. OpenSurvey makes
those first-class and free, in a codebase you fully own. See the [README](../README.md) to get
started in a couple of minutes.
