"""Test settings — uses in-memory SQLite for fast, isolated tests."""
from .base import *  # noqa: F401, F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {
            'SERIALIZE': False,
            'DEPENDENCIES': [],
        },
    }
}

# Speed up tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Minimal logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
}

REDIS_URL = 'redis://localhost:6379/0'
TELEGRAM_BOT_TOKEN = 'test-token'
