"""Root conftest — force test database settings."""
import os
import sys

# Debug: print what settings module is being used
print(f'[conftest] DJANGO_SETTINGS_MODULE = {os.environ.get("DJANGO_SETTINGS_MODULE", "NOT SET")}')

# Force test settings
os.environ['DJANGO_SETTINGS_MODULE'] = 'core.settings.test'

# Now import and configure Django
import django
from django.conf import settings

print(f'[conftest] settings.configured = {settings.configured}')
print(f'[conftest] settings.DATABASES = {getattr(settings, "DATABASES", {})}')

if not settings.configured:
    from core.settings.test import *  # noqa: F403

print(f'[conftest] After import - settings.DATABASES = {settings.DATABASES.get("default", {})}')

if not django.apps.apps.ready:
    django.setup()

from django.db import connections
print(f'[conftest] Connection DB NAME = {connections["default"].settings_dict["NAME"]}')
