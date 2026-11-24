#!/bin/sh
# Render web-service build phase: Python deps → frontend bundle → collected static files.
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"

python -m pip install --upgrade pip
python -m pip install -r requirements/base.txt

sh deploy/frontend/build.sh

DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py collectstatic --noinput
DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py check --deploy || true
