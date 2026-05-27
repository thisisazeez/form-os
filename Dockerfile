# syntax=docker/dockerfile:1
# ============================================================================
# OpenSurvey — container image.
#
# Multi-stage: build the React islands (builder / runtime / widget) with Node,
# then copy the built bundle into the Python/Django runtime. The frontend dist/
# is gitignored, so it MUST be produced here rather than copied from the host.
#
# This image runs `manage.py runserver` under the dev settings for a zero-config
# evaluation/self-host quickstart (Django serves static + media). For a hardened
# production deploy, front it with gunicorn + a static/media server — see the
# "Production notes" section of the README.
# ============================================================================

# ---- Stage 1: build the React islands ----
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Django runtime ----
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.dev
WORKDIR /app

# psycopg[binary] and Pillow ship manylinux wheels (bundled libs) — no apt packages needed.
COPY requirements/ requirements/
RUN pip install -r requirements/base.txt

COPY . .
COPY --from=frontend /app/frontend/dist ./frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["entrypoint.sh"]
