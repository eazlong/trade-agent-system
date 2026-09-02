from channels.generic.websocket import AsyncWebsocketConsumer
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
            await self.close(code=4001)
            return

        from rest_framework_simplejwt.tokens import AccessToken
        from asgiref.sync import sync_to_async

        def _get_user(token_string):
            try:
                access_token = AccessToken(token_string)
                user_id = access_token["user_id"]
            except Exception:
                return None
            from django.contrib.auth import get_user_model

            User = get_user_model()
            try:
                return User.objects.get(id=user_id, is_active=True)
            except Exception:
                return None

        # thread_sensitive=False：避免排在 asgiref 的共享单线程后面。
        # 该单线程会被 FrameManager K 线回调里的阻塞 ccxt 网络调用占住，
        # 导致这里排队挂起、握手永远无法完成（对话"不响应"）。
        user = await sync_to_async(_get_user, thread_sensitive=False)(token_list[0])
        if not user:
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

        # 投递断线期间缓存的待发消息
        pending = await ws_pending.drain(self.user_id)
        if pending:
            logger.info("[ChatWS] Delivering %d pending messages to user %s", len(pending), self.user_id)
            for msg in pending:
                await self.send(text_data=json.dumps(msg, ensure_ascii=False))

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

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get("type")

            if message_type == "chat":
                await self._handle_chat(data)
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
        """发送消息，断线时缓存到 Redis 待重连后投递。返回是否发送成功。"""
        try:
            await self.send(text_data=json.dumps(message, ensure_ascii=False))
            return True
        except Exception:
            user_id = getattr(self, "user_id", "unknown")
            logger.info("[ChatWS] Send failed (client disconnected), buffering for user %s", user_id)
            await ws_pending.store(user_id, message)
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
