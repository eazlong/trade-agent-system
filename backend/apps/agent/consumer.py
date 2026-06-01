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
            except (asyncio.CancelledError, RuntimeError):
                pass
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
                break
            except GeneratorExit:
                break
            except RuntimeError:
                break
            except Exception as e:
                logger.error("[AgentTaskConsumer] poll error: %s", e)
                try:
                    await asyncio.sleep(1)
                except (asyncio.CancelledError, RuntimeError):
                    break

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
            except asyncio.TimeoutError:
                logger.warning(
                    "[AgentTaskConsumer] timeout task_id=%s, retrying", task_id
                )
                await bus.ack(AGENT_TASKS, CG_AGENTS, msg_id)
                await bus.nack_and_retry(AGENT_TASKS, fields, retry_count)
            except Exception as e:
                logger.error(
                    "[AgentTaskConsumer] dispatch error task_id=%s: %s", task_id, e
                )
                # 先 ack 原消息（从 PEL 移除），再按策略重发或写 DLQ
                await bus.ack(AGENT_TASKS, CG_AGENTS, msg_id)
                await bus.nack_and_retry(AGENT_TASKS, fields, retry_count)
            finally:
                from asgiref.sync import sync_to_async
                from django.db import connections
                await sync_to_async(connections.close_all)()

    async def _dispatch(self, fields: dict) -> str:
        import json
        from .base import AgentMessage
        from .supervisor import SupervisorAgent
        from .task_tracker import TaskTracker, tracker_context

        payload_raw = fields.get("payload", "{}")
        payload = (
            json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        )

        user_id = fields.get("user_id", "")
        task_id = fields.get("task_id", "")
        text = payload.get("text", "")

        tracker = TaskTracker(
            task_id=task_id,
            user_id=user_id,
            task_type="agent",
        )
        token = tracker_context.set(tracker)
        try:
            tracker.start(f"收到指令：{text[:80]}")

            msg = AgentMessage(
                sender="channel",
                recipient="supervisor",
                user_id=user_id,
                payload=payload,
                intent=payload.get("intent"),
            )
            supervisor = SupervisorAgent.get_instance()

            # Hard timeout: 280s prevents LLM/tool hangs at source
            result = await asyncio.wait_for(
                supervisor.handle(msg),
                timeout=280,
            )

            if result.success:
                result_text = ""
                if isinstance(result.data, dict) and "content" in result.data:
                    result_text = result.data["content"]
                else:
                    result_text = str(result.data)
                tracker.complete(result_text[:300])
                return result_text
            else:
                tracker.fail(result.error)
                return f"[错误] {result.error}"
        except asyncio.TimeoutError:
            tracker.fail("处理超时（280秒），任务已自动重试")
            raise
        except Exception as e:
            tracker.fail(str(e))
            raise
        finally:
            tracker_context.reset(token)
