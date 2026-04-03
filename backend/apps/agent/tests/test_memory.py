"""Tests for MemoryManager and Agent memory integration."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import unittest

from django.test import TestCase, TransactionTestCase

from apps.memory.manager import MemoryManager, _L1_CACHE


# ------------------------------------------------------------------ #
#  L1 Tests (pure Python, no DB / Redis needed)                       #
# ------------------------------------------------------------------ #

class TestMemoryManagerL1(unittest.TestCase):
    def setUp(self):
        # 隔离L1缓存
        _L1_CACHE.clear()
        self.mem = MemoryManager(agent_type='analyst', user_id='user1')

    def test_write_and_read_l1(self):
        self.mem.write_l1('key1', 'value1')
        self.assertIn('key1', self.mem._l1)
        self.assertEqual(self.mem._l1['key1'], 'value1')

    def test_l1_lru_eviction(self):
        from apps.memory.manager import _L1_MAX
        for i in range(_L1_MAX + 5):
            self.mem.write_l1(f'k{i}', f'v{i}')
        self.assertLessEqual(len(self.mem._l1), _L1_MAX)
        # 最早写入的key已被驱逐
        self.assertNotIn('k0', self.mem._l1)

    def test_l1_update_moves_to_end(self):
        self.mem.write_l1('a', 1)
        self.mem.write_l1('b', 2)
        self.mem.write_l1('a', 99)  # update
        keys = list(self.mem._l1.keys())
        self.assertEqual(keys[-1], 'a')

    def test_l1_isolated_by_agent_and_user(self):
        _L1_CACHE.clear()
        mem2 = MemoryManager(agent_type='analyst', user_id='user2')
        self.mem.write_l1('shared_key', 'user1_val')
        mem2.write_l1('shared_key', 'user2_val')
        self.assertEqual(self.mem._l1['shared_key'], 'user1_val')
        self.assertEqual(mem2._l1['shared_key'], 'user2_val')


# ------------------------------------------------------------------ #
#  L1 retrieve                                                        #
# ------------------------------------------------------------------ #

class TestMemoryManagerRetrieveL1(unittest.TestCase):
    def setUp(self):
        _L1_CACHE.clear()
        self.mem = MemoryManager(agent_type='analyst', user_id='user1')

    def test_retrieve_finds_l1_by_value_substring(self):
        self.mem.write_l1('btc_note', 'BTC is bullish today')
        results = asyncio.run(self.mem.retrieve('BTC', top_k=5))
        self.assertTrue(any(r['source'] == 'l1' for r in results))

    def test_retrieve_finds_l1_by_key_substring(self):
        self.mem.write_l1('eth_signal', 'some value')
        results = asyncio.run(self.mem.retrieve('eth_signal', top_k=5))
        self.assertTrue(any(r['source'] == 'l1' for r in results))

    def test_retrieve_empty_when_no_match(self):
        self.mem.write_l1('btc_note', 'BTC price movement')
        with patch('redis.asyncio.from_url') as mock_redis:
            mock_redis.side_effect = Exception('no redis')
            results = asyncio.run(self.mem.retrieve('ETH', top_k=5))
        self.assertEqual(results, [])

    def test_retrieve_respects_top_k(self):
        for i in range(10):
            self.mem.write_l1(f'key{i}', f'match_{i}')
        results = asyncio.run(self.mem.retrieve('match', top_k=3))
        self.assertLessEqual(len(results), 3)


# ------------------------------------------------------------------ #
#  L2 Tests (mock Redis)                                              #
# ------------------------------------------------------------------ #

class TestMemoryManagerL2(unittest.TestCase):
    def setUp(self):
        _L1_CACHE.clear()
        self.mem = MemoryManager(agent_type='analyst', user_id='user1')

    def _make_mock_redis(self):
        r = AsyncMock()
        r.lpush = AsyncMock(return_value=1)
        r.ltrim = AsyncMock(return_value=True)
        r.expire = AsyncMock(return_value=True)
        r.lrange = AsyncMock(return_value=[])
        r.aclose = AsyncMock()
        return r

    def test_write_l2_calls_redis(self):
        mock_r = self._make_mock_redis()
        with patch('redis.asyncio.from_url', return_value=mock_r):
            asyncio.run(self.mem.write_l2('test content', memory_type='conversation', importance=2))
        mock_r.lpush.assert_called_once()
        mock_r.expire.assert_called_once()

    def test_write_l2_key_format(self):
        mock_r = self._make_mock_redis()
        with patch('redis.asyncio.from_url', return_value=mock_r):
            asyncio.run(self.mem.write_l2('hello'))
        call_args = mock_r.lpush.call_args[0]
        key = call_args[0]
        self.assertEqual(key, 'mem:l2:analyst:user1')

    def test_write_l2_trims_to_100(self):
        mock_r = self._make_mock_redis()
        with patch('redis.asyncio.from_url', return_value=mock_r):
            asyncio.run(self.mem.write_l2('hello'))
        mock_r.ltrim.assert_called_once_with('mem:l2:analyst:user1', 0, 99)

    def test_write_l2_silent_on_redis_error(self):
        with patch('redis.asyncio.from_url', side_effect=Exception('conn refused')):
            # Should not raise
            asyncio.run(self.mem.write_l2('hello'))


# ------------------------------------------------------------------ #
#  L3 Tests (Django ORM, SQLite)                                      #
# ------------------------------------------------------------------ #

class TestMemoryManagerL3(TransactionTestCase):
    def setUp(self):
        _L1_CACHE.clear()
        self.mem = MemoryManager(agent_type='analyst', user_id='user1')

    def test_write_l3_creates_db_record(self):
        asyncio.run(self.mem.write_l3('BTC breakout pattern', metadata={'symbol': 'BTC'}))
        from apps.agent.models import AgentMemory
        obj = AgentMemory.objects.filter(agent_type='analyst', content='BTC breakout pattern').first()
        self.assertIsNotNone(obj)
        self.assertEqual(obj.metadata['symbol'], 'BTC')

    def test_write_l3_default_metadata(self):
        asyncio.run(self.mem.write_l3('plain memory'))
        from apps.agent.models import AgentMemory
        obj = AgentMemory.objects.filter(content='plain memory').first()
        self.assertIsNotNone(obj)
        self.assertEqual(obj.metadata, {})

    def test_retrieve_l3_fallback(self):
        from apps.agent.models import AgentMemory
        AgentMemory.objects.create(agent_type='analyst', content='ETH support level', metadata={})
        # L1 empty, mock L2 as empty
        mock_r = AsyncMock()
        mock_r.lrange = AsyncMock(return_value=[])
        mock_r.aclose = AsyncMock()
        with patch('redis.asyncio.from_url', return_value=mock_r):
            results = asyncio.run(self.mem.retrieve('ETH', top_k=5))
        self.assertTrue(any(r['source'] == 'l3' for r in results))
        self.assertTrue(any('ETH' in r['content'] for r in results))


# ------------------------------------------------------------------ #
#  Agent integration: memory injected into prompt                     #
# ------------------------------------------------------------------ #

class TestLLMAgentMemoryIntegration(unittest.TestCase):
    def setUp(self):
        _L1_CACHE.clear()

    def test_memory_retrieved_and_injected(self):
        """记忆应出现在LLM调用的system prompt中"""
        from apps.agent.sub_agents import AnalystAgent
        from apps.agent.base import AgentMessage

        agent = AnalystAgent()

        # 预写L1记忆
        mem = MemoryManager(agent_type='analyst', user_id='u1')
        mem.write_l1('prior', 'BTC was bullish yesterday')

        captured_system = {}

        async def fake_chat_with_tools(system, messages, tools, max_tokens):
            captured_system['value'] = system
            resp = MagicMock()
            resp.has_tool_calls = False
            resp.content = 'analysis done'
            return resp

        with patch.object(agent._llm, 'chat_with_tools', side_effect=fake_chat_with_tools):
            with patch('redis.asyncio.from_url', side_effect=Exception('no redis')):
                asyncio.run(agent.handle(AgentMessage(
                    sender='user',
                    recipient='analyst',
                    payload={'text': 'BTC analysis'},
                    user_id='u1',
                )))

        self.assertIn('BTC was bullish yesterday', captured_system.get('value', ''))

    def test_memory_written_after_response(self):
        """成功响应后应将对话写入L1"""
        from apps.agent.sub_agents import AnalystAgent
        from apps.agent.base import AgentMessage

        _L1_CACHE.clear()
        agent = AnalystAgent()

        async def fake_chat_with_tools(system, messages, tools, max_tokens):
            resp = MagicMock()
            resp.has_tool_calls = False
            resp.content = 'bullish signal'
            return resp

        with patch.object(agent._llm, 'chat_with_tools', side_effect=fake_chat_with_tools):
            with patch('redis.asyncio.from_url', side_effect=Exception('no redis')):
                msg = AgentMessage(
                    sender='user',
                    recipient='analyst',
                    payload={'text': 'Check BTC'},
                    user_id='u42',
                )
                asyncio.run(agent.handle(msg))

        key = 'analyst:u42'
        self.assertIn(key, _L1_CACHE)
        l1 = _L1_CACHE[key]
        stored = next(iter(l1.values()))
        self.assertIn('Check BTC', stored)
        self.assertIn('bullish signal', stored)
