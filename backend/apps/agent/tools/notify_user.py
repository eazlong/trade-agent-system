"""Agent 主动通知用户的工具。"""

from __future__ import annotations

import logging

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class NotifyUserTool(BaseTool):
    """
    Agent 主动通知用户的工具。

    当任务完成、发现重要信号、或需要用户关注时，Agent 调用此工具
    主动推送消息给用户。user_id 从执行上下文自动注入。
    """

    name = "notify_user"
    description = (
        "主动通知用户。当任务完成、发现重要信息或需要用户关注时使用。"
        "通知内容应简洁明了，包含关键信息。"
        "不要用它做普通对话回复——那些放在正常回复中即可。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "通知内容，简洁明了",
                },
            },
            "required": ["message"],
        }

    async def execute(
        self,
        message: str = "",
        _user_id: str = "",
        **kwargs,
    ) -> ToolResult:
        # user_id 由 base.py 自动注入，兼容旧签名
        user_id = kwargs.get("user_id", _user_id)

        if not message or not message.strip():
            return ToolResult(success=False, error="message 不能为空")

        if not user_id:
            return ToolResult(success=False, error="无法确定通知目标用户")

        try:
            # 持久化到 Notification 模型
            await self._persist_notification(user_id, message)

            # 通过 Redis pubsub 推送（ASGI consumer 路由到活跃渠道）
            self._send_via_redis(user_id, message)

            logger.info(
                "[NotifyUserTool] notification sent to user %s: %s",
                user_id,
                message[:100],
            )
            return ToolResult(
                success=True,
                data={"message": "通知已发送"},
            )
        except Exception as e:
            logger.error("[NotifyUserTool] failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"发送通知失败: {e}")

    @staticmethod
    async def _persist_notification(user_id: str, message: str) -> None:
        """写入 Notification 模型，留存历史记录。"""
        from asgiref.sync import sync_to_async
        from django.contrib.auth import get_user_model

        from apps.notify.models import Notification

        @sync_to_async
        def _create():
            User = get_user_model()
            user = None
            try:
                import uuid

                user = User.objects.get(id=uuid.UUID(str(user_id)))
            except (ValueError, User.DoesNotExist):
                # 渠道侧 id（open_id / telegram_id / username）也要能落到正确的用户，
                # 否则飞书发起的会话通知不会出现在 web 通知中心。
                user = (
                    User.objects.filter(username=user_id).first()
                    or User.objects.filter(feishu_open_id=user_id).first()
                    or User.objects.filter(telegram_id=user_id).first()
                )
            if user:
                Notification.objects.create(
                    user=user,
                    channel="web",
                    message=message,
                )

        await _create()

    @staticmethod
    def _send_via_redis(user_id: str, text: str) -> None:
        """通过 Redis pubsub 发布通知，由 ASGI consumer 消费并路由到用户活跃渠道。"""
        import json

        import redis
        from django.conf import settings
        from django.utils import timezone

        from apps.agent.task_tracker import _PROGRESS_CHANNEL

        url = settings.REDIS_URL
        if url.rsplit("/", 1)[-1].isdigit():
            url = url.rsplit("/", 1)[0] + "/3"

        try:
            r = redis.from_url(url, decode_responses=True)
            r.publish(
                _PROGRESS_CHANNEL,
                json.dumps(
                    {
                        "user_id": user_id,
                        "text": text,
                        "at": timezone.now().isoformat(),
                    }
                ),
            )
        except Exception:
            logger.warning(
                "[NotifyUserTool] Redis pubsub delivery failed", exc_info=True
            )
