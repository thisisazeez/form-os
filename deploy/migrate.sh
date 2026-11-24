#!/bin/sh
# Release step: provision/update the shared schema and ensure a demo tenant exists.
# Run this after a deploy (Render one-off job, `vercel env pull && sh deploy/migrate.sh`,
# or locally against the deployment DB). Never run it per web request.
set -eu

: "${DATABASE_URL:?DATABASE_URL must point at the deployment Postgres database}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.prod}"

echo "==> Shared migrations"
python manage.py migrate_schemas --shared

echo "==> Demo tenant (idempotent)"
python manage.py create_demo_tenant || true

echo "==> Collect static assets"
python manage.py collectstatic --noinput
