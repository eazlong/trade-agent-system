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

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose'},
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
    'loggers': {
        'httpx':    {'level': 'WARNING', 'handlers': ['console'], 'propagate': False},
        'httpcore': {'level': 'WARNING', 'handlers': ['console'], 'propagate': False},
        'telegram': {'level': 'WARNING', 'handlers': ['console'], 'propagate': False},
    },
}
