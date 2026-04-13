from channels.generic.websocket import AsyncWebsocketConsumer
import json
import logging

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncWebsocketConsumer):
    """
    通用通知 WebSocket 消费者，用于向客户端推送实时更新
    包括交易状态、数据更新、系统通知等
    """

    async def connect(self):
        # 拒绝未认证用户的连接
        if self.scope["user"].is_anonymous:
            await self.close()
        else:
            await self.accept()
            # 加入用户特定组
            self.user_group = f"user_{self.scope['user'].id}"
            await self.channel_layer.group_add(self.user_group, self.channel_name)
            logger.info(f"[WebSocket] User {self.scope['user'].id} connected")

    async def disconnect(self, close_code):
        # 从用户组中移除
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)
        logger.info(f"[WebSocket] User {self.scope['user'].id} disconnected")

    async def receive(self, text_data):
        """接收来自客户端的消息"""
        try:
            data = json.loads(text_data)
            message_type = data.get("type")

            if message_type == "subscribe":
                # 处理订阅请求
                subscription_type = data.get("subscription_type")
                if subscription_type:
                    await self.channel_layer.group_add(
                        f"{subscription_type}_updates", self.channel_name
                    )
                    await self.send(
                        text_data=json.dumps(
                            {
                                "type": "subscription_ack",
                                "subscription_type": subscription_type,
                                "status": "success",
                            }
                        )
                    )

            elif message_type == "unsubscribe":
                # 处理取消订阅请求
                subscription_type = data.get("subscription_type")
                if subscription_type:
                    await self.channel_layer.group_discard(
                        f"{subscription_type}_updates", self.channel_name
                    )
        except json.JSONDecodeError:
            logger.error("[WebSocket] Invalid JSON received")
        except Exception as e:
            logger.error(f"[WebSocket] Error processing message: {e}")

    async def send_notification(self, event):
        """发送通知到 WebSocket 客户端"""
        await self.send(text_data=json.dumps(event["message"]))
