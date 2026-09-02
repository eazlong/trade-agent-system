from enum import Enum
from typing import Optional, Dict, Any
import time
import json
import redis.asyncio as aioredis
from django.conf import settings


class SessionState(Enum):
    """会话状态枚举"""

    NONE = "none"  # 空闲，所有消息走意图识别
    MULTI_TURN = "multi_turn"  # 多轮对话中，特定Agent接管
    PAUSED = "paused"  # 多轮对话被临时中断
    WORKFLOW_RUNNING = "workflow_running"  # 工作流执行中


class SessionManager:
    """会话状态管理器"""

    def __init__(self):
        self._redis = None

    async def get_redis(self):
        if self._redis is None:
            self._redis = await aioredis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
        return self._redis

    async def get_session_context(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户会话上下文"""
        r = await self.get_redis()
        key = f"session:{user_id}:context"
        data = await r.get(key)

        if data:
            context = json.loads(data)
            # 检查会话是否过期
            if context.get("expires_at", 0) < time.time():
                await r.delete(key)  # 清理会话
                return None
            return context
        return None

    async def set_session_context(
        self,
        user_id: str,
        state: SessionState,
        active_agent: Optional[str] = None,
        task_id: Optional[str] = None,
        ttl: int = 28800,
    ) -> None:  # 默认8小时过期
        """设置用户会话上下文

        Args:
            task_id: 工作流任务 ID（WORKFLOW_RUNNING 时用于取消）
        """
        r = await self.get_redis()
        key = f"session:{user_id}:context"

        context = {
            "state": state.value,
            "active_agent": active_agent,
            "expires_at": time.time() + ttl,
            "updated_at": time.time(),
        }
        if task_id:
            context["task_id"] = task_id

        await r.setex(key, ttl, json.dumps(context, ensure_ascii=False))

    async def pause_session(
        self,
        user_id: str,
        active_agent: str,
        pause_ttl: int = 14400,
        pause_context: str = "",
    ) -> None:
        """暂停多轮对话会话"""
        r = await self.get_redis()
        key = f"session:{user_id}:context"

        context = {
            "state": SessionState.PAUSED.value,
            "active_agent": active_agent,
            "paused_at": time.time(),
            "pause_ttl": pause_ttl,
            "pause_context": pause_context,
            "expires_at": time.time() + pause_ttl,
            "updated_at": time.time(),
        }

        await r.setex(key, pause_ttl, json.dumps(context, ensure_ascii=False))

    async def resume_session(
        self, user_id: str, agent_name: str, ttl: int = 86400
    ) -> None:
        """恢复暂停的会话为多轮对话"""
        r = await self.get_redis()
        key = f"session:{user_id}:context"

        context = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": agent_name,
            "expires_at": time.time() + ttl,
            "updated_at": time.time(),
        }

        await r.setex(key, ttl, json.dumps(context, ensure_ascii=False))

    async def clear_session_context(self, user_id: str) -> None:
        """清除用户会话上下文"""
        r = await self.get_redis()
        key = f"session:{user_id}:context"
        await r.delete(key)


# 全局实例
_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    return _session_manager
