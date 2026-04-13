"""Tests for core module."""

from __future__ import annotations

from django.test import TestCase


class TestCoreSettings(TestCase):
    """测试核心配置"""

    def test_test_settings_loaded(self):
        """测试环境使用 test settings"""
        from django.conf import settings

        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3"
        )

    def test_installed_apps(self):
        from django.conf import settings

        expected_apps = [
            "apps.core",
            "apps.authentication",
        ]
        for app in expected_apps:
            self.assertIn(app, settings.INSTALLED_APPS)

    def test_rest_framework_config(self):
        from django.conf import settings

        self.assertIn("REST_FRAMEWORK", dir(settings))
        auth_classes = settings.REST_FRAMEWORK.get("DEFAULT_AUTHENTICATION_CLASSES", [])
        self.assertTrue(len(auth_classes) > 0)
