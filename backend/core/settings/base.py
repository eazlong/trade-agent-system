import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv()

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-insecure-change-in-production')

DEBUG = False

ALLOWED_HOSTS = []

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    # Third-party
    'rest_framework',
    'corsheaders',
    'channels',
    # Core app
    'apps.core',
    # Apps
    'apps.authentication',
    'apps.agent',
    'apps.skill',
    'apps.exchange',
    'apps.trading',
    'apps.risk',
    'apps.backtest',
    'apps.memory',
    'apps.channel',
    'apps.notify',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'
ASGI_APPLICATION = 'core.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'trade_agent'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'options': '-c search_path=public'},
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://localhost:6379/1'),
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [os.environ.get('REDIS_URL', 'redis://localhost:6379')],
        },
    },
}

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')

# Redis DB allocation
# DB0: Django cache
# DB1: Channel layers
# DB2: Celery broker
# DB3: Agent task queue (Redis Stream)
# DB4: Trading order queue (Redis Stream)
# DB5: Position cache
# DB6: Agent working memory (TTL)
# DB7: RiskGuard circuit breaker
REDIS_DB_CACHE = 0
REDIS_DB_CELERY = 2
REDIS_DB_AGENT_STREAM = 3
REDIS_DB_TRADING_STREAM = 4
REDIS_DB_POSITION = 5
REDIS_DB_MEMORY = 6
REDIS_DB_RISK = 7

CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379') + '/2'
CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://localhost:6379') + '/2'
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = 'UTC'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '20/min',
        'user': '200/min',
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}

CORS_ALLOWED_ORIGINS = os.environ.get('CORS_ALLOWED_ORIGINS', 'http://localhost:3000').split(',')

AUTH_USER_MODEL = 'authentication.User'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_TZ = True

# LLM
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
OPENAI_MODEL_PRIMARY = os.environ.get('OPENAI_MODEL_PRIMARY', 'gpt-4o')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL_FALLBACK = os.environ.get('ANTHROPIC_MODEL_FALLBACK', 'claude-opus-4-6')
OPENAI_API_BASE_URL = os.environ.get('OPENAI_API_BASE_URL', 'https://api.openai.com/v1/')
LLM_TOKEN_BUDGET = int(os.environ.get('LLM_TOKEN_BUDGET', '100000'))

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_ADMIN_CHAT_ID = os.environ.get('TELEGRAM_ADMIN_CHAT_ID', '')

# Proxies (each can be set independently; fall back to HTTPS_PROXY / HTTP_PROXY)
_default_proxy = os.environ.get('HTTPS_PROXY', '') or os.environ.get('HTTP_PROXY', '')
TELEGRAM_PROXY  = os.environ.get('TELEGRAM_PROXY', '') or _default_proxy
OPENAI_PROXY    = os.environ.get('OPENAI_PROXY', '')   or _default_proxy
ANTHROPIC_PROXY = os.environ.get('ANTHROPIC_PROXY', '') or _default_proxy
WEB_PROXY            = os.environ.get('WEB_PROXY', '')           or _default_proxy

# Web tools
WEB_SEARCH_PROVIDER  = os.environ.get('WEB_SEARCH_PROVIDER', 'duckduckgo')  # brave|tavily|searxng|jina|duckduckgo
WEB_SEARCH_MAX_RESULTS = int(os.environ.get('WEB_SEARCH_MAX_RESULTS', '5'))
WEB_FETCH_MAX_CHARS  = int(os.environ.get('WEB_FETCH_MAX_CHARS', '8000'))
BRAVE_SEARCH_API_KEY = os.environ.get('BRAVE_SEARCH_API_KEY', '')
TAVILY_API_KEY       = os.environ.get('TAVILY_API_KEY', '')
SEARXNG_BASE_URL     = os.environ.get('SEARXNG_BASE_URL', '')
JINA_API_KEY         = os.environ.get('JINA_API_KEY', '')
