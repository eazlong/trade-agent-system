from channels.generic.websocket import AsyncWebsocketConsumer
import asyncio
import json
import logging
import traceback
from urllib.parse import parse_qs

from apps.agent import ws_pending
from apps.agent.base import AgentMessage, AgentResult
from apps.agent.supervisor import SupervisorAgent

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    """SupervisorAgent WebSocket 聊天消费者

    客户端通过 WebSocket 发送聊天消息，SupervisorAgent 处理后返回响应。
    断线期间的消息会缓存到 Redis，重连后自动投递。

    协议格式:
    - 发送: {"type": "chat", "text": "用户消息"}
    - 接收: {"type": "chat_response", "data": "回复内容", "task_id": "...", "status": "done|error"}
    - 接收: {"type": "status", "status": "connected|processing"}
    """

    async def connect(self):
        # 从 query string 解析 JWT token
        query_string = self.scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        token_list = query_params.get("token", [])

        if not token_list:
            logger.warning("[ChatWS] Connection rejected: missing token")
            await self.close(code=4001)
            return

        # 走通用线程池 + 刷新目标线程的 DB 连接：
        # - thread_sensitive=False: 不排在 asgiref 共享单线程后面，否则
        #   FrameManager K 线回调的阻塞 ccxt 调用会占死单线程、握手挂死。
        # - db_async: 通用线程池里的 DB 连接是 thread-local，长时间空闲后
        #   pgbouncer / Postgres 会单方面关闭 → "server closed the
        #   connection unexpectedly"，必须先 close_old_connections()。
        from apps.core.db_utils import db_async
        from apps.notify.middleware import authenticate_token

        user, reason = await db_async(authenticate_token)(token_list[0])
        if not user:
            logger.warning("[ChatWS] Connection rejected: %s", reason)
            await self.close(code=4001)
            return

        await self.accept()
        self.user_id = str(user.id)
        logger.info("[ChatWS] User %s connected", self.user_id)

        # 加入 user_{user_id} group — 接收 Celery 任务完成通知（_route_notification 推送）
        await self.channel_layer.group_add(
            f"user_{self.user_id}", self.channel_name
        )

        await self.send(text_data=json.dumps({"type": "status", "status": "connected"}))

        # Subscribe before replay; a concurrent group wake uses the same delivery ID.
        self._delivered = set()
        try:
            for message in await ws_pending.peek(self.user_id):
                await self.web_delivery({"message": message})
        except Exception:
            logger.warning("[ChatWS] pending replay failed", exc_info=True)

    async def disconnect(self, close_code):
        user_id = getattr(self, "user_id", "unknown")
        # 退出 user_{user_id} group
        if user_id != "unknown" and hasattr(self, "channel_layer"):
            try:
                await self.channel_layer.group_discard(
                    f"user_{user_id}", self.channel_name
                )
            except Exception:
                pass
        logger.info("[ChatWS] User %s disconnected (code=%s)", user_id, close_code)

    async def task_notification(self, event):
        """Celery / Stream 任务完成通知 — 透传到 ws。

        event = {"type": "task_notification", "text": "..."}
        前端识别 {"type": "task_notification", "data": "..."}（后续 PR 加 UI 渲染）。
        """
        await self._safe_send({
            "type": "task_notification",
            "data": event["text"],
        })

    # Strong references keep workflows alive across socket disconnects.
    # Process restarts still interrupt these tasks; this is not a durable job queue.
    _running_chats: set[asyncio.Task] = set()

    @classmethod
    def _chat_finished(cls, task):
        cls._running_chats.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("[ChatWS] background chat failed: %s", task.exception())

    async def web_delivery(self, event):
        """Deliver a user-group envelope locally, never rebroadcast it."""
        message = event["message"]
        delivery_id = message.get("delivery_id")
        if delivery_id and delivery_id in self._delivered:
            return
        try:
            await self.send(text_data=json.dumps(message, ensure_ascii=False))
        except Exception:
            # Already buffered by push_web_payload; another socket/reconnect can deliver.
            return
        if delivery_id:
            self._delivered.add(delivery_id)
            if len(self._delivered) > 4096:
                self._delivered = {delivery_id}
        try:
            await ws_pending.ack(self.user_id, message)
        except Exception:
            logger.warning("[ChatWS] delivery ack failed", exc_info=True)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get("type")

            if message_type == "chat":
                active = getattr(self, "_chat_task", None)
                if active is not None and not active.done():
                    await self._safe_send({"type": "error", "error": "当前连接仍有任务处理中"})
                    return
                self._chat_task = asyncio.create_task(self._handle_chat(data))
                self._running_chats.add(self._chat_task)
                self._chat_task.add_done_callback(self._chat_finished)
            elif message_type == "ping":
                await self._safe_send({"type": "pong"})
            else:
                await self._safe_send(
                    {"type": "error", "error": f"Unknown message type: {message_type}"}
                )

        except json.JSONDecodeError:
            logger.error("[ChatWS] Invalid JSON received")
            await self._safe_send({"type": "error", "error": "Invalid JSON"})
        except Exception as e:
            logger.error("[ChatWS] Error processing message: %s", e)
            await self._safe_send({"type": "error", "error": str(e)})

    async def _safe_send(self, message: dict) -> bool:
        """Terminal replies target current user connections, not the originating socket."""
        if message.get("type") in {"chat_response", "task_submitted"}:
            from apps.agent.reply_fanout import push_web_payload
            await push_web_payload(self.user_id, message)
            return True  # queued, not a browser acknowledgement
        try:
            await self.send(text_data=json.dumps(message, ensure_ascii=False))
            return True
        except Exception:
            return False

    async def _handle_chat(self, data):
        text = data.get("text", "").strip()
        if not text:
            await self._safe_send({"type": "error", "error": "text is required"})
            return

        logger.info("[ChatWS] User %s chat: %s", self.user_id, text[:100])

        # processing 状态是瞬时的，不做缓存
        await self._safe_send({"type": "status", "status": "processing"})

        async def on_tool_result(tool_name, result_text):
            """工具执行进度回调 — 实时推送给前端，断线直接丢弃。"""
            try:
                await self.send(text_data=json.dumps({
                    "type": "tool_progress",
                    "tool": tool_name,
                    "result": result_text[:2000],
                }, ensure_ascii=False))
            except Exception:
                pass

        try:
            supervisor = SupervisorAgent.get_instance()
            result: AgentResult = await supervisor.handle(
                AgentMessage(
                    sender="user",
                    recipient="supervisor",
                    payload={"text": text},
                    user_id=self.user_id,
                ),
                on_tool_result=on_tool_result,
            )

            # 检测回测/异步任务提交 → 推送结构化确认
            if (
                isinstance(result.data, dict)
                and result.data.get("task_id")
                and result.success
            ):
                await self._safe_send({
                    "type": "task_submitted",
                    "task_id": result.data["task_id"],
                    "status": "success",
                })

            if result.success:
                content = (
                    result.data.get("content", str(result.data))
                    if isinstance(result.data, dict)
                    else result.data
                )
                await self._safe_send({
                    "type": "chat_response",
                    "data": content,
                    "task_id": result.task_id,
                    "status": "done",
                })

                # 多通道扇出：web 已由 chat_response 送达，这里把回复同时推送到
                # 主通道（MAIN_CHANNEL，默认飞书）。例如回测后「已安排 5 分钟后
                # 自动查询结果」需要飞书与 web 端同时收到。
                try:
                    from apps.agent.reply_fanout import fan_out_reply

                    await fan_out_reply(
                        self.user_id,
                        content if isinstance(content, str) else str(content),
                        origin="web",
                    )
                except Exception:
                    logger.warning(
                        "[ChatWS] fan_out_reply failed for user %s",
                        self.user_id,
                        exc_info=True,
                    )
            else:
                await self._safe_send({
                    "type": "chat_response",
                    "error": result.error or "处理请求时发生错误，请稍后重试",
                    "task_id": result.task_id,
                    "status": "error",
                })

        except Exception as e:
            logger.error(
                "[ChatWS] Supervisor handle error: %s\n%s", e, traceback.format_exc()
            )
            await self._safe_send({
                "type": "chat_response",
                "error": str(e),
                "status": "error",
            })
