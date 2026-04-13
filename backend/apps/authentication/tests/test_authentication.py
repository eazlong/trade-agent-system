"""Tests for authentication models and serializers."""

from __future__ import annotations

from django.test import TestCase

from apps.authentication.models import User


class TestUserModel(TestCase):
    """测试 User 模型"""

    def test_create_user(self):
        user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
            username="testuser",
        )
        self.assertEqual(user.email, "test@example.com")
        self.assertTrue(user.check_password("testpass123"))
        self.assertFalse(user.is_admin)

    def test_create_superuser(self):
        user = User.objects.create_superuser(
            email="admin@example.com",
            password="adminpass123",
            username="admin",
        )
        self.assertTrue(user.is_admin)

    def test_email_normalized(self):
        """Django's BaseUserManager.normalize_email lowercases domain"""
        email = "Test@Example.COM"
        user = User.objects.create_user(
            email=email,
            password="pass123",
            username="testnorm",
        )
        self.assertEqual(user.email, "Test@example.com")

    def test_string_representation(self):
        user = User.objects.create_user(
            email="rep@example.com",
            password="pass123",
            username="repuser",
        )
        self.assertEqual(str(user), "repuser")

    def test_unique_email(self):
        User.objects.create_user(
            email="unique@example.com",
            password="pass123",
            username="user1",
        )
        with self.assertRaises(Exception):
            User.objects.create_user(
                email="unique@example.com",
                password="anotherpass",
                username="user2",
            )


class TestUserSerializer(TestCase):
    """测试认证序列化器"""

    def setUp(self):
        self.user = User.objects.create_user(
            email="ser@example.com",
            password="serpass123",
            username="seruser",
        )

    def test_user_serializer_includes_fields(self):
        from apps.authentication.serializers import UserSerializer

        serializer = UserSerializer(instance=self.user)
        data = serializer.data
        self.assertIn("id", data)
        self.assertIn("email", data)
        self.assertIn("username", data)

    def test_login_serializer_validation(self):
        from apps.authentication.serializers import LoginSerializer

        serializer = LoginSerializer(
            data={
                "email": "ser@example.com",
                "password": "serpass123",
            }
        )
        self.assertTrue(serializer.is_valid())

    def test_login_serializer_invalid_password(self):
        from apps.authentication.serializers import LoginSerializer

        serializer = LoginSerializer(
            data={
                "email": "ser@example.com",
                "password": "wrong_password",
            }
        )
        self.assertFalse(serializer.is_valid())
