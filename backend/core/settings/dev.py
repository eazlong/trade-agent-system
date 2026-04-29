import os
from pathlib import Path

from .base import *  # noqa

DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS += ['django_extensions']  # noqa

# Use SQLite for local dev without PostgreSQL, or PostgreSQL if configured
if os.environ.get('DB_HOST'):
    # Docker / PostgreSQL environment
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'trade_agent'),
            'USER': os.environ.get('DB_USER', 'trade'),
            'PASSWORD': os.environ.get('DB_PASSWORD', 'trade'),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
        }
    }
else:
    # Local dev without PostgreSQL
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db_dev.sqlite3',
        }
    }

# dev settings override: root logger at DEBUG
LOGGING['root']['level'] = 'DEBUG'  # noqa: F405
