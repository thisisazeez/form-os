#!/bin/sh
# Render web-service start command. Render injects PORT and DATABASE_URL.
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"

# Migrations are idempotent; for multi-instance production move these to a one-off job
# (deploy/migrate.sh) and remove them from the request path.
python manage.py migrate_schemas --shared
python manage.py create_demo_tenant || true

exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout 120 \
  --access-logfile -
