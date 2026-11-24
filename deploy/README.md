# Deployment

One split, two platforms:

| Piece | Platform | Config |
|---|---|---|
| **API + Postgres** | Render (free, no card) | manual Web Service + PostgreSQL below; `deploy/render/render.yaml` is the optional Blueprint version |
| **Frontend assets** | Vercel | `frontend/vercel.json` (static project, Root Directory = `frontend`) |

The Django API serves the real pages (`/`, `/landing`, `/auth/login`, `/<org>/`). The Vercel
project publishes `frontend/dist` — React islands, `widget/opensurvey-widget.js`, self-hosted
fonts and brand SVGs — as an asset host. For the demo, use the **Render service URL** as the
single public URL.

## Render API — no card needed (manual setup)

**Skip Blueprints** — Render asks for a card to use them. Create the two free resources
directly instead; free web services, free Postgres and static sites don't require a card.

1. **Create the database:** Render Dashboard → **New → PostgreSQL** → name it `onform-db`,
   choose region, compute plan **Free** → **Create Database**. Copy its connection string
   from the dashboard (the “Internal Database URL” works if the web service is in the same
   region; otherwise use the external one).
2. **Create the web service:** **New → Web Service** → connect your repo → name it
   `onform-api` → same region as the DB → runtime **Python** → compute plan **Free**.
   - Build command: `./deploy/render/api-build.sh`
   - Start command: `./deploy/render/api-start.sh`
   - Health check path: `/healthz` (optional)
   - Add the environment variables from the table below, including `DATABASE_URL` pasted
     from step 1. Generate `SECRET_KEY` with `openssl rand -hex 32`.
3. Open `https://<your-service>.onrender.com/` — public landing page.
   Demo login: `demo@onform.example` / `DemoPass123!` (created on startup).
   Health check: `/healthz`.

`DATABASE_URL` is supplied by Render when you create the managed Postgres — the Blueprint
just automates pasting it into the web service. With manual setup you copy it once yourself.
`config/settings/base.py` reads it via `django-environ` and forces the django-tenants
Postgres backend.

Scripts used: `deploy/render/api-build.sh` (deps + frontend bundle + collectstatic),
`deploy/render/api-start.sh` (idempotent migrations + demo tenant + gunicorn), and
`deploy/frontend/build.sh` (shared frontend build).

### Free-tier gotchas
- Free web services spin down after ~15 min idle; the next request takes ~1 min to wake.
  Open the URL once before you present.
- Free Postgres expires after **30 days** and has 1 GB / no backups — fine for a demo,
  not for real data.
- Free web services can't run one-off jobs or SSH; that's why migrations run in the start
  script. SMTP ports are blocked, so the console email backend is used.

### Optional: Blueprint with a card
`deploy/render/render.yaml` is kept for later. It automates the same two resources and
injects `DATABASE_URL` via `fromDatabase`, but Render currently requires a card to use
Blueprints.

## Vercel frontend

Create a Vercel project with **Root Directory = `frontend`**, then:

```sh
sh deploy/vercel/deploy-frontend.sh
```

`frontend/vercel.json` builds with `npm ci && npm run build`, publishes `dist/` and sets
long-cache headers for fonts and hashed bundles.

## Environment variables (Render web service)

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Copy from your Render `onform-db` dashboard (Postgres is required — django-tenants cannot use SQLite); the Blueprint injects it automatically if you use that optional path |
| `SECRET_KEY` | Generate locally (`openssl rand -hex 32`) and paste into the service; the Blueprint can generate it instead |
| `DEBUG` | `false` |
| `ALLOWED_HOSTS` | Comma-separated hostnames, e.g. `onform-api.onrender.com` |
| `BASE_DOMAIN` | Same host as above (path-based tenant routing, ADR-001) |
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `DJANGO_VITE_DEV_MODE` | `false` — serve the built manifest |
| `CELERY_TASK_ALWAYS_EAGER` | `true` only if no Celery worker is deployed |
| `SECURE_SSL_REDIRECT` | `true` behind Render's TLS terminator |
| `EMAIL_BACKEND` | console backend is fine for a demo; configure SMTP before real signups |

## Pricing note

The landing page shows a single plan — self-hosted free, ₦0/mo (NGN). There is **no hosted
tier, payment gateway or subscription engine** in v1: the pricing CTA leads to the
self-hosting docs, the header/hero CTAs lead to signup, and the in-app billing page reports
the same free self-hosted plan.
