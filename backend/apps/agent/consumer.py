from __future__ import annotations

import asyncio
import logging
import socket

from . import bus
from .bus import AGENT_TASKS, CG_AGENTS

logger = logging.getLogger(__name__)

_CONSUMER_NAME = f"supervisor-{socket.gethostname()}"


class AgentTaskConsumer:
    """
    消费 agent:tasks stream，路由到 SupervisorAgent，将结果写回 reply 信箱。
    在 ASGI 启动时作为后台 asyncio task 运行。
    """

    def __init__(self, concurrency: int = 4):
        self._concurrency = concurrency
        self._running = False
        self._semaphore: asyncio.Semaphore | None = None
        self._poll_task: asyncio.Task | None = None
        self._handle_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        await bus.ensure_groups()
        self._running = True
        self._semaphore = asyncio.Semaphore(self._concurrency)
        logger.info("[AgentTaskConsumer] started consumer=%s", _CONSUMER_NAME)
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                logger.info("[AgentTaskConsumer] Polling task cancelled")
        if self._handle_tasks:
            for t in list(self._handle_tasks):
                t.cancel()
            await asyncio.gather(*self._handle_tasks, return_exceptions=True)
        logger.info("[AgentTaskConsumer] stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                messages = await bus.consume(
                    AGENT_TASKS,
                    CG_AGENTS,
                    _CONSUMER_NAME,
                    count=self._concurrency,
                    block_ms=5000,
                )
                for msg_id, fields in messages:
                    task = asyncio.create_task(self._handle(msg_id, fields))
                    self._handle_tasks.add(task)
                    task.add_done_callback(self._handle_tasks.discard)
            except asyncio.CancelledError:
                logger.info("[AgentTaskConsumer] Poll loop cancelled")
                break
            except Exception as e:
                logger.error("[AgentTaskConsumer] poll error: %s", e)
                await asyncio.sleep(1)

    async def _handle(self, msg_id: str, fields: dict) -> None:
        async with self._semaphore:
            task_id = fields.get("task_id", msg_id)
            retry_count = int(fields.get("retry_count", 0))
            try:
                result_text = await self._dispatch(fields)
                await bus.ack(AGENT_TASKS, CG_AGENTS, msg_id)
                await bus.publish_reply(task_id, result_text)
            except asyncio.CancelledError:
                logger.info(
                    "[AgentTaskConsumer] Handle task cancelled for task_id=%s", task_id
                )
                raise
            except Exception as e:
                logger.error(
                    "[AgentTaskConsumer] dispatch error task_id=%s: %s", task_id, e
                )
                # 先 ack 原消息（从 PEL 移除），再按策略重发或写 DLQ
                await bus.ack(AGENT_TASKS, CG_AGENTS, msg_id)
                await bus.nack_and_retry(AGENT_TASKS, fields, retry_count)

    async def _dispatch(self, fields: dict) -> str:
        import json
        from .base import AgentMessage
        from .supervisor import SupervisorAgent

        payload_raw = fields.get("payload", "{}")
        payload = (
            json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        )

        user_id = fields.get("user_id", "")
        msg = AgentMessage(
            sender="channel",
            recipient="supervisor",
            user_id=user_id,
            payload=payload,
            intent=payload.get("intent"),
        )
        supervisor = SupervisorAgent.get_instance()
        result = await supervisor.handle(msg)
        if result.success:
            # 如果结果是字典格式，且包含content字段，则返回content
            if isinstance(result.data, dict) and "content" in result.data:
                return result.data["content"]
            return str(result.data)
        return f"[错误] {result.error}"
