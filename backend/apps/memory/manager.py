from __future__ import annotations

import calendar
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from apps.memory.keys import MemoryKey
from apps.memory.redis_client import RedisPool

logger = logging.getLogger(__name__)

# L1: 进程内 LRU 缓存
_L1_CACHE: dict[str, OrderedDict[str, Any]] = {}
_L1_MAX = 50


class MemoryManager:
    """分层记忆管理器 — L1(进程内) / L2(Redis) / L3(PostgreSQL)"""

    def __init__(self, agent_type: str, user_id: str, agent_name: Optional[str] = None):
        self.agent_type = agent_type
        self.user_id = user_id
        self.agent_name = agent_name or agent_type  # 来源标记
        self._l1_key = MemoryKey.conv_history(user_id)
        self._redis = None
        try:
            self._redis = RedisPool.get_client()
        except Exception as e:
            logger.warning("RedisPool init failed, L2 disabled: %s", e)

        if self._l1_key not in _L1_CACHE:
            _L1_CACHE[self._l1_key] = OrderedDict()

        self._l1_lock = threading.Lock()

    @property
    def _l1(self) -> OrderedDict:
        """线程安全的 L1 访问"""
        if self._l1_key not in _L1_CACHE:
            with self._l1_lock:
                if self._l1_key not in _L1_CACHE:
                    _L1_CACHE[self._l1_key] = OrderedDict()
        return _L1_CACHE[self._l1_key]

    # ====== L1 写入 ======
    def write_l1(self, key: str, value: Any) -> None:
        """写入L1（进程内，最快）"""
        with self._l1_lock:
            od = self._l1
            if key in od:
                od.move_to_end(key)
            od[key] = value
            if len(od) > _L1_MAX:
                od.popitem(last=False)
        logger.debug("[MEM] write_l1 | user=%s agent=%s | key=%s",
                    self.user_id, self.agent_name, key)

    # ====== L2 写入 ======
    async def write_l2(
        self,
        content: str,
        memory_type: str = "general",
        importance: int = 1,
        shared: bool = True,
    ) -> None:
        """写入L2（Redis），默认同时写入私有区和共享区"""
        if self._redis is None:
            logger.debug("L2 write skipped (Redis unavailable)")
            return
        try:
            entry = {
                "content": content,
                "type": memory_type,
                "importance": importance,
                "ts": time.time(),
                "source_agent": self.agent_name,
            }
            # 私有记忆
            p_key = MemoryKey.l2_private(self.agent_name, self.user_id)
            await self._redis.lpush(p_key, json.dumps(entry))
            await self._redis.ltrim(p_key, 0, 99)
            await self._redis.expire(p_key, 86400)
            logger.debug("[MEM] write_l2 private | user=%s agent=%s | key=%s | content=%s",
                        self.user_id, self.agent_name, p_key, content)
            # 共享记忆
            if shared:
                s_key = MemoryKey.l2_shared(self.user_id)
                await self._redis.lpush(s_key, json.dumps(entry))
                await self._redis.ltrim(s_key, 0, 199)
                await self._redis.expire(s_key, 86400)
                logger.debug("[MEM] write_l2 shared | user=%s agent=%s | key=%s | content=%s",
                            self.user_id, self.agent_name, s_key, content)
        except Exception as e:
            logger.warning("L2 write failed: %s", e)

    # ====== L3 写入 ======
    async def write_l3(self, content: str, metadata: Optional[dict] = None) -> None:
        """写入L3（PostgreSQL，异步ORM）"""
        try:
            from apps.agent.models import AgentMemory
            from asgiref.sync import sync_to_async

            obj = await sync_to_async(AgentMemory.objects.create)(
                agent_type=self.agent_type,
                agent_name=self.agent_name,
                user_id=self.user_id,
                content=content,
                metadata=metadata or {},
            )
            logger.debug("[MEM] write_l3 | user=%s agent=%s | id=%s | content=%s",
                        self.user_id, self.agent_name, obj.id, content)
        except Exception as e:
            logger.warning("L3 write failed: %s", e)

    # ====== 对话历史读写 ======
    async def save_conv_history(self, history: list) -> None:
        """保存对话历史到 L1 + L2 双写"""
        if self._redis is None:
            logger.debug("save_conv_history L2 skipped (Redis unavailable)")
            self.write_l1("conv_history", history)
            logger.info(
                "[MEM] save_conv_history L1 only | user=%s agent=%s | turns=%d | latest=%s",
                self.user_id, self.agent_name, len(history),
                history[-1] if history else "N/A",
            )
            return
        try:
            self.write_l1("conv_history", history)
            await self._redis.set(
                MemoryKey.conv_history(self.user_id),
                json.dumps(history),
                ex=86400,
            )
            logger.debug(
                "[MEM] save_conv_history L1+L2 | user=%s agent=%s | turns=%d | latest=%s",
                self.user_id, self.agent_name, len(history),
                history[-1] if history else "N/A",
            )
        except Exception as e:
            logger.debug("save_conv_history L2 failed, L1 still ok: %s", e)

    async def get_conv_history(self, max_turns: int = 10) -> list:
        """读取对话历史，L1 → L2 → 旧key 降级"""
        redis_available = self._redis is not None
        try:
            # L1 锁读
            with self._l1_lock:
                conv = self._l1.get("conv_history")
                if conv is not None:
                    return await self._log_conv_history_result("L1", conv[-max_turns * 2 :])

            # L1 miss，读 L2
            if redis_available:
                try:
                    raw = await self._redis.get(MemoryKey.conv_history(self.user_id))
                    if raw:
                        conv = json.loads(raw)
                        self.write_l1("conv_history", conv)  # 回填 L1
                        return await self._log_conv_history_result("L2", conv[-max_turns * 2 :])
                except Exception as e:
                    logger.debug("L2 conv read failed: %s", e)

                # L2 miss，降级读旧 key（双读兼容）
                try:
                    old_key = f"conv:supervisor:{self.user_id}"
                    raw = await self._redis.get(old_key)
                    if raw:
                        conv = json.loads(raw)
                        self.write_l1("conv_history", conv)
                        return await self._log_conv_history_result("L2(old_key)", conv[-max_turns * 2 :])
                except Exception as e:
                    logger.debug("Old key conv read failed: %s", e)

            return await self._log_conv_history_result("MISS", [])
        except Exception as e:
            logger.debug("get_conv_history failed: %s", e)
            return []

    async def _log_conv_history_result(self, source: str, conv: list) -> list:
        """统一记录对话历史读取结果"""
        logger.info(
            "[MEM] get_conv_history | source=%s | user=%s agent=%s | turns=%d | history=%s",
            source, self.user_id, self.agent_name, len(conv), conv,
        )
        return conv

    # ====== 检索 ======
    async def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """按优先级检索并统一按时间降序排序：L1 -> L2私有 -> L2共享 -> L3"""
        results: list[dict] = []
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 2]
        seen: set[str] = set()  # 去重

        # L1
        with self._l1_lock:
            for k, v in reversed(list(self._l1.items())):
                text = (k + " " + str(v)).lower()
                if query_words and any(w in text for w in query_words):
                    entry = {"source": "l1", "key": k, "content": str(v), "ts": 0}
                    if str(v) not in seen:
                        results.append(entry)
                        seen.add(str(v))

        # L2 私有
        if self._redis is not None:
            results = await self._retrieve_l2_private(
                query_lower, results, top_k, query_words, seen
            )

        # L2 共享
        if self._redis is not None:
            results = await self._retrieve_l2_shared(
                query_lower, results, top_k, query_words, seen
            )

        # L3
        results = await self._retrieve_l3(query, results, top_k, seen)

        # P1-01: 按时间戳降序排序后截断
        results.sort(key=lambda r: r.get("ts", 0), reverse=True)
        final = results[:top_k]
        logger.info(
            "[MEM] retrieve | user=%s agent=%s | query=%s | found=%d/%d | results=%s",
            self.user_id, self.agent_name, query, len(final), top_k, final,
        )
        return final

    async def _retrieve_l2_private(
        self,
        query_lower: str,
        results: list,
        top_k: int,
        query_words: list,
        seen: set,
    ) -> list:
        """查询 L2 私有记忆"""
        if self._redis is None:
            logger.debug("L2 private retrieve skipped (Redis unavailable)")
            return results
        try:
            p_key = MemoryKey.l2_private(self.agent_name, self.user_id)
            raw_list = await self._redis.lrange(p_key, 0, 49)
            for raw in raw_list:
                entry = json.loads(raw)
                text = entry.get("content", "").lower()
                if query_words and any(w in text for w in query_words):
                    if entry.get("content") not in seen:
                        results.append({"source": "l2", **entry})
                        seen.add(entry.get("content"))
                elif query_lower in text:
                    if entry.get("content") not in seen:
                        results.append({"source": "l2", **entry})
                        seen.add(entry.get("content"))
                if len(results) >= top_k:
                    return results
        except Exception as e:
            logger.debug("L2 private retrieve skipped: %s", e)
        return results

    async def _retrieve_l2_shared(
        self,
        query_lower: str,
        results: list,
        top_k: int,
        query_words: list,
        seen: set,
    ) -> list:
        """查询 L2 共享记忆"""
        if self._redis is None:
            logger.debug("L2 shared retrieve skipped (Redis unavailable)")
            return results
        try:
            s_key = MemoryKey.l2_shared(self.user_id)
            raw_list = await self._redis.lrange(s_key, 0, 49)
            for raw in raw_list:
                entry = json.loads(raw)
                # 排除自己写的（已在私有区查过）
                if entry.get("source_agent") == self.agent_name:
                    continue
                text = entry.get("content", "").lower()
                if query_words and any(w in text for w in query_words):
                    if entry.get("content") not in seen:
                        results.append({"source": "l2_shared", **entry})
                        seen.add(entry.get("content"))
                elif query_lower in text:
                    if entry.get("content") not in seen:
                        results.append({"source": "l2_shared", **entry})
                        seen.add(entry.get("content"))
                if len(results) >= top_k:
                    return results
        except Exception as e:
            logger.debug("L2 shared retrieve skipped: %s", e)
        return results

    async def _retrieve_l3(
        self, query: str, results: list, top_k: int, seen: set
    ) -> list:
        """查询 L3 长期记忆"""
        if len(results) >= top_k:
            return results
        try:
            from apps.agent.models import AgentMemory
            from asgiref.sync import sync_to_async

            qs = await sync_to_async(
                lambda: list(
                    AgentMemory.objects.filter(
                        user_id=self.user_id,
                        content__icontains=query,
                    ).order_by("-created_at")[: top_k - len(results)]
                )
            )()
            for obj in qs:
                if obj.content not in seen:
                    ts = (
                        calendar.timegm(obj.created_at.timetuple())
                        if obj.created_at
                        else 0
                    )
                    results.append(
                        {
                            "source": "l3",
                            "content": obj.content,
                            "metadata": obj.metadata,
                            "agent_name": getattr(obj, "agent_name", ""),
                            "ts": ts,
                        }
                    )
                    seen.add(obj.content)
        except Exception as e:
            logger.debug("L3 retrieve skipped: %s", e)
        return results
