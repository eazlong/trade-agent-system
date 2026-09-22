"""订单异常的统一告警口（第①段强制前置）。

CONTEXT.md 对「告警」的定义是**给一个具体的人的即时消息，不是一条日志，也不是
一个新系统**。所以这里只做两件本来就存在的事，不新建投递机制：

1. 落一条 ``notify.Notification``（持久、Web 通知中心可见）；
2. 通过 ``task_tracker`` 的 Redis pubsub 推一条即时消息（ASGI consumer 按用户活跃
   渠道路由）。

**没有接收人就不算告警**：``order.user`` 为空时无法确定「受影响用户」，本模块返回
``False`` 并记 ERROR，由调用方计入扫描结果（该结果进任务健康检查），而不是假装
送达。把「告警」实现成 ``logger.error`` 正是这条主线要排除的东西——日志不算被看见。

本模块是**出站的唯一通知口**（``notify_user``）：订单异常、风控拒单、下单失败、快照
写入失败都走它，不新增第二个投递机制。原先 ``RiskGuard.record_order_failure`` 自己
内联写 ``Notification``（只落库、不推送），2026-09-22 合并到此——代价是订单失败从此
多一条即时推送，那是刻意的：只在 Web 通知中心可见的告警，用户不看就等于没有。
"""

from __future__ import annotations

import logging

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

_MSG_HEADER = "⚠️ 订单状态异常"


def format_order_anomaly(order, reason: str) -> str:
    """把订单异常渲染成一条给用户看的即时消息。"""
    short_id = str(order.id)[:8]
    return (
        f"{_MSG_HEADER}\n"
        f"订单 #{short_id}｜{order.symbol}｜{order.side}｜数量 {order.quantity}\n"
        f"{reason}"
    )


@sync_to_async
def _persist(user_id, message: str) -> None:
    from apps.notify.models import Notification

    Notification.objects.create(user_id=user_id, channel="web", message=message)


@sync_to_async
def _publish(user_id: str, message: str) -> None:
    from apps.agent.task_tracker import _send_notification_redis

    _send_notification_redis(user_id, message)


async def notify_user(user_id, message: str) -> bool:
    """把一条消息投递给**一个具体的人**；这是本仓库唯一的用户通知口。

    Returns:
        True 表示已投递给一个具体的人；False 表示没有接收人或推送失败（已记日志）。
    """
    if not user_id:
        logger.error("[Notify] 无接收人，消息未送达：%s", message)
        return False

    # 两个通道各自独立：持久化失败不该吞掉即时消息，反之亦然。
    try:
        await _persist(user_id, message)
    except Exception:  # noqa: BLE001
        logger.warning("[Notify] 通知落库失败 user=%s", user_id, exc_info=True)
    try:
        await _publish(str(user_id), message)
    except Exception:  # noqa: BLE001
        logger.warning("[Notify] 通知推送失败 user=%s", user_id, exc_info=True)
        return False
    return True


async def alert_order_anomaly(order, reason: str) -> bool:
    """把一条订单异常告警投递给**受影响用户**。

    Returns:
        True 表示已投递给一个具体的人；False 表示无法确定接收人（已记 ERROR）。
    """
    if not order.user_id:
        logger.error(
            "[OrderAlert] 订单 %s 状态异常但无关联用户，无法告警：%s",
            order.id,
            reason,
        )
        return False
    return await notify_user(order.user_id, format_order_anomaly(order, reason))
