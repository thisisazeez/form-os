#!/usr/bin/env python
"""Django's command-line utility for administrative tasks.

Defaults to the dev settings module; override with DJANGO_SETTINGS_MODULE.
django-tenants ships extra commands (e.g. `migrate_schemas`, `create_tenant`,
`tenant_command`) once it's in INSTALLED_APPS.
"""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
