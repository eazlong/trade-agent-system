"""Tests for exchange models and utils."""

from __future__ import annotations


from django.test import TestCase

from apps.exchange.models import ExchangeAccount
from apps.exchange.utils import get_exchange_account


class TestExchangeAccountModel(TestCase):
    """测试 ExchangeAccount 模型"""

    def test_create_exchange_account(self):
        account = ExchangeAccount.objects.create(
            exchange="binance",
            label="test_account",
            api_key_enc=b"encrypted_key_data",
            api_secret_enc=b"encrypted_secret_data",
            is_active=True,
        )
        self.assertEqual(account.exchange, "binance")
        self.assertEqual(account.label, "test_account")
        self.assertTrue(account.is_active)
        self.assertIsNotNone(account.id)
        self.assertIsNotNone(account.created_at)

    def test_string_representation(self):
        account = ExchangeAccount.objects.create(
            exchange="okx",
            label="my_okx",
            api_key_enc=b"key",
            api_secret_enc=b"secret",
        )
        self.assertIn("okx", str(account))
        self.assertIn("my_okx", str(account))

    def test_inactive_account(self):
        account = ExchangeAccount.objects.create(
            exchange="bybit",
            label="frozen",
            is_active=False,
        )
        self.assertFalse(account.is_active)


class TestGetExchangeAccount(TestCase):
    """测试获取交易所账户"""

    def test_returns_none_for_nonexistent_account(self):
        result = get_exchange_account("00000000-0000-0000-0000-000000000000")
        self.assertIsNone(result)

    def test_returns_none_for_inactive_account(self):
        account = ExchangeAccount.objects.create(
            exchange="binance",
            label="test",
            is_active=False,
        )
        result = get_exchange_account(str(account.id))
        self.assertIsNone(result)

    def test_returns_account_when_active(self):
        account = ExchangeAccount.objects.create(
            exchange="binance",
            label="test_active",
            api_key_enc=b"encrypted_key",
            api_secret_enc=b"encrypted_secret",
            is_active=True,
        )
        result = get_exchange_account(str(account.id))
        self.assertIsNotNone(result)
        self.assertEqual(result.exchange, "binance")
