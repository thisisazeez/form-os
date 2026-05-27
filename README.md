# OpenSurvey

**OpenSurvey** is a self-hosted, multi-tenant survey & experience-management platform built with
Django and React. Create surveys with a visual builder, share them as links (or embed them on your
site/app), branch with conditional logic, collect and analyze responses, and manage contacts,
segments, teams, and roles — all from a single, fully-owned, open-source codebase.

It’s a self-hosted alternative to proprietary survey SaaS: features that are usually paywalled
(Teams & RBAC, Contacts/Segments, webhooks, API keys, white-label) are first-class here.

## Screenshots

<p align="center">
  <img src="docs/screenshots/builder.png" alt="OpenSurvey survey builder — question palette, settings, and a live preview" width="900">
</p>

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/dashboard.png" alt="Dashboard"><br><sub><b>Dashboard</b> — active surveys, responses, completion rate & NPS at a glance.</sub></td>
    <td width="50%"><img src="docs/screenshots/results.png" alt="Results & analytics"><br><sub><b>Results & analytics</b> — completion funnel, drop-off, and response trends.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/runtime.png" alt="Public survey runtime"><br><sub><b>Public survey</b> — what respondents see (shareable link or embed).</sub></td>
    <td width="50%"><img src="docs/screenshots/contacts.png" alt="Contacts & segments"><br><sub><b>Contacts & segments</b> — typed attributes, dynamic segments, PII masking.</sub></td>
  </tr>
</table>

> Screenshots can be regenerated against any running instance with `scripts/capture_screenshots.mjs`.

## Features

- **Survey builder** — visual editor with 15+ question types (text, single/multi-select, rating,
  NPS, picture choice, matrix, ranking, date, contact info, …), a live preview, welcome cards,
  custom ending screens, and per-question **conditional logic** with a visual flow map.
- **Styling & theming** — brand color, backgrounds, logo, card layout; per-survey or workspace-wide.
- **Public runtime** — share a survey as a link, embed it via `<iframe>`, or render it in-product
  via the embeddable **JavaScript widget** (website & app surveys with action/event triggers).
- **Responses & analytics** — response table, per-response detail, completion funnel, drop-off,
  NPS/rating summaries, responses-over-time, CSV/JSON export, tags & filters.
- **Contacts & segments** — typed contact attributes, dedup, PII masking, and dynamic segments
  with a live-preview filter builder.
- **Teams & RBAC** — organizations with owner/admin/member roles plus workspace-level teams with
  read/write/manage access; default-deny enforcement throughout.
- **Multi-tenancy** — schema-per-organization isolation via `django-tenants`, path-based routing
  (`/<org-slug>/…`).
- **Platform** — webhooks (signed), API keys, audit log, Slack integration, response quotas,
  follow-up emails, a survey templates gallery, and multi-language authoring.

## Tech stack

- **Backend:** Python 3.11+ (developed on 3.13), Django 5.1, Django REST Framework, `django-tenants`
  (schema-per-tenant), PostgreSQL, Redis + Celery.
- **Frontend:** server-rendered HTMX + Alpine + Tailwind shell, with **React islands** (the survey
  builder, the public runtime, and the embeddable widget) bundled by Vite via `django-vite`.
- **Survey logic** is defined as JSON validated against a frozen schema and evaluated by twin
  TypeScript (runtime) and Python (server) engines kept in lock-step by a shared fixture corpus.

> PostgreSQL is **required** — `django-tenants` uses schema-per-tenant and does not support SQLite.

---

## Quick start

Two ways to run it — pick one.

### Option A — Everything with Docker (fastest)

Requires only **Docker**. One command builds the app (including the React bundles), starts
PostgreSQL + Redis, runs migrations, creates a `demo` organization, and serves the app:

```bash
docker compose --profile app up --build
```

When you see `Starting OpenSurvey`, open **http://127.0.0.1:8000**. Create an account at
**/auth/signup** — since dev uses the console email backend, the verification link is printed in
the `web` container’s logs (`docker compose logs -f web`); open it to verify, then log in.

Stop with `docker compose --profile app down` (add `-v` to also wipe the database volume).

> This image runs Django’s dev server for a zero-config quickstart. For production, see
> [Production notes](#production-notes).

### Option B — Run manually (recommended for development)

Requires **Docker** (just for PostgreSQL + Redis), **Python 3.11+**, and **Node 18+**.

#### 1. Start PostgreSQL + Redis (Docker)

```bash
docker compose up -d        # postgres:16 on :5432, redis:7 on :6379
```

(If you prefer your own Postgres/Redis, skip this and point `DATABASE_URL` / `CELERY_BROKER_URL` at
them instead.)

#### 2. Configure the environment

```bash
cp .env.example .env        # then edit if your DB/Redis/host differ
```

See [Configuration](#configuration) for every variable. The defaults in `.env.example` match the
`docker-compose.yml` services, so for local development you can usually leave it as-is.

#### 3. Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
```

#### 4. Migrate the database

```bash
python manage.py migrate_schemas --shared      # public (shared) schema
python manage.py create_demo_tenant            # creates a "demo" organization + its schema
```

`create_demo_tenant` accepts `--slug` / `--name` (e.g. `--slug acme --name "Acme Inc"`).

#### 5. Build the frontend (React islands)

```bash
cd frontend
npm install
npm run build                       # builds the builder + runtime + widget bundles into dist/
cd ..
```

For frontend development with hot-reload instead: set `DJANGO_VITE_DEV_MODE=True` in `.env` and run
`npm run dev` in `frontend/` alongside the Django server.

#### 6. Run the server

```bash
python manage.py runserver          # http://127.0.0.1:8000
```

Now:

- Visit **http://127.0.0.1:8000/** → it redirects to the login page.
- Create an account at **/auth/signup**. In development the email backend is the **console**, so the
  verification link is printed to the terminal running the server — open it to verify, then log in.
- After login you land in the app for your organization at **/<org-slug>/**.

#### 7. (Optional) Background worker

The dev settings run Celery tasks **synchronously** (`CELERY_TASK_ALWAYS_EAGER=True`), so you don’t
need a worker for local use. To run tasks for real (e.g. follow-up emails):

```bash
celery -A config worker -l info
```

---

## Configuration

Settings are read from environment variables (and an optional `.env` file) via `django-environ`, in
`config/settings/`. A starter `.env.example` is checked in — copy it to `.env`. The app boots with
safe defaults even if a variable is unset.

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.dev` | Settings module. Use `config.settings.dev` for local dev. |
| `SECRET_KEY` | `dev-insecure-change-me` | Django secret key. **Set a real one in production.** |
| `DEBUG` | `False` (dev settings default `True`) | Debug mode. Keep `False` in production. |
| `ALLOWED_HOSTS` | empty (dev: `localhost,127.0.0.1,.localhost`) | Comma-separated allowed hosts. Required in production. |
| `BASE_DOMAIN` | `localhost` | Base host that organizations are served under. |
| `DATABASE_URL` | `postgres://opensurvey:opensurvey@localhost:5432/opensurvey` | PostgreSQL connection (required; no SQLite). |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Redis broker for Celery. |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | Celery result backend. |
| `CELERY_TASK_ALWAYS_EAGER` | `True` (dev) | Run tasks synchronously (no worker needed) — set `False` in production. |
| `PUBLIC_BASE_URL` | dev: `http://127.0.0.1:8011` | Absolute base URL used in outgoing email links. Set to your real URL. |
| `MEDIA_URL` / `MEDIA_ROOT` | `/media/` · `<repo>/media` | Where uploaded media (e.g. profile avatars) is served/stored. |
| `DJANGO_VITE_DEV_MODE` | `False` | `True` proxies to the Vite dev server (HMR); `False` uses the built manifest. |

### Email (SMTP)

Email is **provider-agnostic** and read from the environment. With no SMTP configured, OpenSurvey
uses Django’s **console backend** (emails print to stdout — perfect for local dev, zero setup).

| Variable | Default | Description |
|---|---|---|
| `EMAIL_HOST` | empty → console backend | SMTP host. Set this to enable real sending. |
| `EMAIL_PORT` | `587` | SMTP port. |
| `EMAIL_HOST_USER` | empty | SMTP username. |
| `EMAIL_HOST_PASSWORD` | empty | SMTP password. |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | `True` / `False` | TLS / SSL. |
| `DEFAULT_FROM_EMAIL` | `no-reply@example.com` | Default “from” address. |
| `POSTMARK_SERVER_TOKEN` | empty | Optional convenience: pre-fills Postmark’s SMTP host/credentials. |

> Note: the checked-in `.env.example` predates the SMTP variables above — add them to your `.env`
> if you need real email. Any standard SMTP provider works; Postmark is not required.

---

## Running tests

```bash
docker compose up -d                 # PostgreSQL must be running (tests use real schemas)
source .venv/bin/activate
python -m pytest                     # backend suite

cd frontend && npm test              # frontend (vitest)
```

Lint: `ruff check .`

---

## How multi-tenancy works

Each organization gets its own PostgreSQL schema (`django-tenants`). The tenant is resolved from the
**first URL path segment** against the organization slug — `/<org-slug>/...` — by a small custom
middleware (`apps/common/middleware.py`). Shared data (users, organizations, memberships) lives in
the `public` schema; everything else (surveys, responses, contacts, …) lives per-organization.
Because Postgres forbids cross-schema foreign keys, tenant tables store the user’s UUID as a plain
column and resolve it against the public schema in Python.

## Project layout

```
config/        Django project: settings (base/dev/ci), URL confs (public + tenant), Celery, WSGI/ASGI
apps/          Django apps — accounts, organizations (public schema); workspaces, teams, surveys,
               responses, contacts, segments, actions, integrations, webhooks, … (tenant schemas)
frontend/      Vite + React islands (builder, runtime, widget) + the shared TS logic engine
templates/     HTMX + Alpine + Tailwind server-rendered shell, auth pages, admin pages
tests/         pytest suite (+ the shared TS/Python logic fixture corpus)
contracts/     Frozen interface contracts (data model, OpenAPI, survey/logic JSON schema, RBAC)
docker-compose.yml   PostgreSQL + Redis for local development
```

---

## Production notes

The defaults are tuned for local development. Before deploying:

- Set a strong `SECRET_KEY`, `DEBUG=False`, and an explicit `ALLOWED_HOSTS`.
- Configure real SMTP (`EMAIL_HOST`, …) and a correct `PUBLIC_BASE_URL`.
- Run Celery with a real broker (`CELERY_TASK_ALWAYS_EAGER=False` + a worker process).
- Serve behind a real WSGI server (e.g. gunicorn) and a reverse proxy; serve `MEDIA_ROOT` via your
  web server or object storage rather than Django.
- Run `python manage.py collectstatic` and `python manage.py check --deploy`.

## License

Released under the **MIT License** — use it for anything, commercial or otherwise. See
[`LICENSE`](LICENSE).

## Contributing

Issues and pull requests are welcome. Please run `ruff check .`, the pytest suite, and the frontend
build before submitting.
