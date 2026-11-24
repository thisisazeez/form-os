#!/bin/sh
# onForm container entrypoint: migrate the shared schema, ensure a demo org exists,
# then serve. The db service is gated by a healthcheck in docker-compose, so Postgres is
# ready by the time this runs.
set -e

echo "==> Applying shared (public) migrations..."
python manage.py migrate_schemas --shared

echo "==> Ensuring the 'demo' organization exists..."
python manage.py create_demo_tenant || true

echo "==> Starting onForm on http://0.0.0.0:8000 ..."
exec python manage.py runserver 0.0.0.0:8000
