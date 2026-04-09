import os
from pathlib import Path

from .base import *  # noqa

DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS += ['django_extensions']  # noqa

# Use SQLite for local dev (no PostgreSQL required)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db_dev.sqlite3',
    }
}

# dev settings override: root logger at DEBUG
LOGGING['root']['level'] = 'DEBUG'  # noqa: F405
