"""
信号监控 conftest

为所有测试提供 Django 设置和公共 fixtures。
"""
import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.test')


def pytest_configure(config):
    """确保 Django 设置在测试开始前初始化"""
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            DATABASES={
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': ':memory:',
                }
            },
            INSTALLED_APPS=[
                'django.contrib.auth',
                'django.contrib.contenttypes',
                'rest_framework',
                'rest_framework_simplejwt',
                'apps.core',
                'apps.authentication',
                'apps.backtest',
                'apps.exchange',
                'apps.trading',
                'apps.notify',
                'apps.signal_monitor',
            ],
            ROOT_URLCONF='core.urls',
            SECRET_KEY='test-secret-key',
            DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
            AUTH_USER_MODEL='authentication.User',
            USE_TZ=True,
            TIME_ZONE='UTC',
            REST_FRAMEWORK={
                'DEFAULT_AUTHENTICATION_CLASSES': [
                    'rest_framework_simplejwt.authentication.JWTAuthentication',
                ],
                'DEFAULT_PERMISSION_CLASSES': [
                    'rest_framework.permissions.IsAuthenticated',
                ],
            },
            SIMPLE_JWT={
                'ACCESS_TOKEN_LIFETIME': __import__('datetime').timedelta(hours=1),
                'REFRESH_TOKEN_LIFETIME': __import__('datetime').timedelta(days=7),
            },
        )
    django.setup()
