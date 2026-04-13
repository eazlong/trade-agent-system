"""Tests for GetExchangeAccountTool."""
from __future__ import annotations

import asyncio

from django.test import TestCase

from apps.agent.tools.exchange_account import GetExchangeAccountTool
from apps.exchange.models import ExchangeAccount


class TestGetExchangeAccountTool(TestCase):
    """测试交易所账号查询工具"""

    def setUp(self):
        """创建测试数据"""
        ExchangeAccount.objects.create(
            exchange='binance',
            label='binance_main',
            api_key_enc=b'key1',
            api_secret_enc=b'secret1',
            is_active=True,
        )
        ExchangeAccount.objects.create(
            exchange='okx',
            label='okx_main',
            api_key_enc=b'key2',
            api_secret_enc=b'secret2',
            is_active=True,
        )
        ExchangeAccount.objects.create(
            exchange='bybit',
            label='bybit_test',
            api_key_enc=b'key3',
            api_secret_enc=b'secret3',
            is_active=False,
        )

    def _run(self, **kwargs):
        return asyncio.get_event_loop().run_until_complete(
            GetExchangeAccountTool().execute(**kwargs),
        )

    def test_list_all_active_accounts(self):
        """查询所有活跃账号"""
        result = self._run()
        self.assertTrue(result.success)
        self.assertIn('2 个', result.data)
        self.assertIn('binance_main', result.data)
        self.assertIn('okx_main', result.data)
        # 不活跃的不应该出现
        self.assertNotIn('bybit_test', result.data)

    def test_filter_by_exchange(self):
        """按交易所类型筛选"""
        result = self._run(exchange='binance')
        self.assertTrue(result.success)
        self.assertIn('1 个', result.data)
        self.assertIn('binance_main', result.data)
        self.assertNotIn('okx_main', result.data)

    def test_filter_by_label(self):
        """按标签关键词模糊匹配"""
        result = self._run(label='main')
        self.assertTrue(result.success)
        self.assertIn('2 个', result.data)
        self.assertIn('binance_main', result.data)
        self.assertIn('okx_main', result.data)

    def test_no_match_returns_friendly_message(self):
        """无匹配结果返回友好提示"""
        result = self._run(exchange='bitget')
        self.assertTrue(result.success)
        self.assertIn('未找到', result.data)
        self.assertIn('bitget', result.data)

    def test_no_accounts_at_all(self):
        """数据库中无任何账号时的提示"""
        ExchangeAccount.objects.all().delete()
        result = self._run()
        self.assertTrue(result.success)
        self.assertIn('未找到', result.data)
