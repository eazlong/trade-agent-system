from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

# L1: 进程内 LRU，最多保留50条/agent
_L1_CACHE: dict[str, OrderedDict[str, Any]] = {}
_L1_MAX = 50


class MemoryManager:
    """
    分层记忆管理器。
    L1: 进程内 OrderedDict（LRU）
    L2: Redis（短期，TTL 24h）
    L3: PostgreSQL + pgvector（长期语义记忆）
    """

    def __init__(self, agent_type: str, user_id: str):
        self.agent_type = agent_type
        self.user_id = user_id
        self._l1_key = f'{agent_type}:{user_id}'
        if self._l1_key not in _L1_CACHE:
            _L1_CACHE[self._l1_key] = OrderedDict()

    @property
    def _l1(self) -> OrderedDict:
        if self._l1_key not in _L1_CACHE:
            _L1_CACHE[self._l1_key] = OrderedDict()
        return _L1_CACHE[self._l1_key]

    # ------------------------------------------------------------------ #
    #  写入                                                               #
    # ------------------------------------------------------------------ #

    def write_l1(self, key: str, value: Any) -> None:
        """写入L1（进程内，最快）"""
        od = self._l1
        if key in od:
            od.move_to_end(key)
        od[key] = value
        if len(od) > _L1_MAX:
            od.popitem(last=False)

    async def write_l2(self, content: str, memory_type: str = 'general',
                      importance: int = 1, shared: bool = True) -> None:
        """写入L2（Redis），默认同时写入私有区和共享区"""
        try:
            import redis.asyncio as aioredis
            from django.conf import settings
            r = aioredis.from_url(settings.REDIS_URL)
            entry = {
                'content': content,
                'type': memory_type,
                'importance': importance,
                'ts': time.time(),
                'source_agent': self.agent_type,
            }
            # 私有记忆
            key = f'mem:l2:{self.agent_type}:{self.user_id}'
            await r.lpush(key, json.dumps(entry))
            await r.ltrim(key, 0, 99)
            await r.expire(key, 86400)
            # 共享记忆
            if shared:
                shared_key = f'mem:l2:shared:{self.user_id}'
                await r.lpush(shared_key, json.dumps(entry))
                await r.ltrim(shared_key, 0, 199)
                await r.expire(shared_key, 86400)
            await r.aclose()
        except Exception as e:
            logger.warning(f'L2 write failed: {e}')

    async def write_l3(self, content: str, metadata: Optional[dict] = None) -> None:
        """写入L3（PostgreSQL，异步ORM）"""
        try:
            from apps.agent.models import AgentMemory
            from asgiref.sync import sync_to_async
            await sync_to_async(AgentMemory.objects.create)(
                agent_type=self.agent_type,
                content=content,
                metadata=metadata or {},
            )
        except Exception as e:
            logger.warning(f'L3 write failed: {e}')

    # ------------------------------------------------------------------ #
    #  检索                                                               #
    # ------------------------------------------------------------------ #

    async def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """按优先级：L1 -> L2私有 -> L2共享 -> L3"""
        results: list[dict] = []
        query_words = [w for w in query.lower().split() if len(w) > 2]

        # L1 私有
        for k, v in reversed(list(self._l1.items())):
            text = (k + ' ' + str(v)).lower()
            if query_words and any(w in text for w in query_words):
                results.append({'source': 'l1', 'key': k, 'content': str(v)})
            if len(results) >= top_k:
                return results

        # L2 私有
        results = await self._retrieve_l2_private(query, results, top_k, query_words)
        if len(results) >= top_k:
            return results

        # L2 共享
        results = await self._retrieve_l2_shared(query, results, top_k, query_words)
        if len(results) >= top_k:
            return results

        # L3
        results = await self._retrieve_l3(query, results, top_k)

        return results[:top_k]

    async def _retrieve_l2_private(self, query: str, results: list, top_k: int, query_words: list) -> list:
        """查询 L2 私有记忆"""
        try:
            import redis.asyncio as aioredis
            from django.conf import settings
            r = aioredis.from_url(settings.REDIS_URL)
            raw_list = await r.lrange(f'mem:l2:{self.agent_type}:{self.user_id}', 0, 49)
            await r.aclose()
            for raw in raw_list:
                entry = json.loads(raw)
                text = entry.get('content', '').lower()
                if query_words and any(w in text for w in query_words):
                    results.append({'source': 'l2', **entry})
                elif query.lower() in text:
                    results.append({'source': 'l2', **entry})
                if len(results) >= top_k:
                    return results
        except Exception as e:
            logger.debug(f'L2 private retrieve skipped: {e}')
        return results

    async def _retrieve_l2_shared(self, query: str, results: list, top_k: int, query_words: list) -> list:
        """查询 L2 共享记忆"""
        try:
            import redis.asyncio as aioredis
            from django.conf import settings
            r = aioredis.from_url(settings.REDIS_URL)
            shared_key = f'mem:l2:shared:{self.user_id}'
            raw_list = await r.lrange(shared_key, 0, 49)
            await r.aclose()
            for raw in raw_list:
                entry = json.loads(raw)
                # 排除自己写的（已在私有区查过）
                if entry.get('source_agent') == self.agent_type:
                    continue
                text = entry.get('content', '').lower()
                if query_words and any(w in text for w in query_words):
                    results.append({'source': 'l2_shared', **entry})
                elif query.lower() in text:
                    results.append({'source': 'l2_shared', **entry})
                if len(results) >= top_k:
                    return results
        except Exception as e:
            logger.debug(f'L2 shared retrieve skipped: {e}')
        return results

    async def _retrieve_l3(self, query: str, results: list, top_k: int) -> list:
        """查询 L3 长期记忆"""
        if len(results) >= top_k:
            return results
        try:
            from apps.agent.models import AgentMemory
            from asgiref.sync import sync_to_async
            qs = await sync_to_async(
                lambda: list(
                    AgentMemory.objects.filter(
                        agent_type=self.agent_type,
                        content__icontains=query,
                    ).order_by('-created_at')[: top_k - len(results)]
                )
            )()
            for obj in qs:
                results.append({
                    'source': 'l3',
                    'content': obj.content,
                    'metadata': obj.metadata,
                })
        except Exception as e:
            logger.debug(f'L3 retrieve skipped: {e}')
        return results
