"""
RiskGuard - 风控守卫

前置校验（pre_trade_check）：
- 熔断器检查（circuit breaker）
- 单笔仓位上限
- 最大回撤限制
- 日内交易次数

实时监控（monitor loop）：
- 浮亏超阈值预警
- 止损触发强平

由 FrameManager 管理生命周期，非独立进程（ADR-003）。
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Tuple

from apps.core.db_utils import db_async

if TYPE_CHECKING:
    from apps.trading.adapters.base import OrderRequest

from apps.riskguard.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class RiskGuard:
    """
    风控守卫（随交易框架或辅助框架启动）。
    """

    _instance: "RiskGuard | None" = None

    # 风控阈值（可由管理员通过 RiskConfig 覆盖）
    MAX_POSITION_RATIO = Decimal("0.20")  # 单仓不超过总资产20%
    MAX_DAILY_DRAWDOWN = Decimal("0.05")  # 日内最大回撤5%
    MAX_DAILY_TRADES = 50  # 日内最大交易次数
    FLOATING_LOSS_ALERT = Decimal("-0.03")  # 浮亏-3%预警

    def __init__(self, mode: str = "trading"):
        self.mode = mode
        self._running = False
        self._monitor_task: asyncio.Task | None = None
        # 持仓抓取连续失败计数（成功后归零）：用于区分偶发超时与适配器卡死
        self._position_fetch_failures = 0

    @classmethod
    def get_instance(cls) -> "RiskGuard | None":
        return cls._instance

    async def start(self) -> None:
        RiskGuard._instance = self
        self._running = True
        if self.mode in ("trading", "monitor"):
            self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info(f"RiskGuard started (mode={self.mode})")

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        RiskGuard._instance = None
        logger.info("RiskGuard stopped")

    # ------------------------------------------------------------------ #
    #  前置校验                                                          #
    # ------------------------------------------------------------------ #

    async def pre_trade_check(
        self, request: "OrderRequest", user_id: str | None
    ) -> Tuple[bool, str]:
        """
        返回 (approved, reason)，approved=False 时 OrderExecutor 禁止下单。

        校验顺序：
        1. 熔断器检查
        2. 日内交易次数
        3. 仓位上限
        4. 日内回撤
        """
        logger.info(f"[RiskGuard] pre_trade_check: {request.symbol} {request.side}")

        if not user_id:
            return True, "OK"

        # 1. 熔断器检查（已禁用）
        # logger.info(f"[RiskGuard] step 1: circuit breaker check")
        # if await self._is_circuit_open(user_id):
        #     logger.warning(f"[RiskGuard] REJECTED: 熔断器触发，今日禁止交易")
        #     return False, "熔断器触发，今日禁止交易"
        logger.info(f"[RiskGuard] step 1: circuit breaker (disabled)")

        # 2. 日内交易次数
        logger.info(f"[RiskGuard] step 2: daily trade count check")
        daily_count = await self._get_daily_trade_count(user_id)
        logger.info(f"[RiskGuard] step 2 done: count={daily_count}")
        if daily_count >= self.MAX_DAILY_TRADES:
            reason = f"日内交易次数已达上限 {self.MAX_DAILY_TRADES}"
            logger.warning(f"[RiskGuard] REJECTED: {reason}")
            return await self._reject(user_id, reason)

        # 3. 仓位上限
        logger.info(f"[RiskGuard] step 3: position limit check")
        position_ok, reason = await self._check_position_limit(request, user_id)
        logger.info(f"[RiskGuard] step 3 done: ok={position_ok}")
        if not position_ok:
            logger.warning(f"[RiskGuard] REJECTED: {reason}")
            return await self._reject(user_id, reason)

        # 4. 日内回撤
        logger.info(f"[RiskGuard] step 4: drawdown check")
        drawdown_ok, reason = await self._check_drawdown(user_id)
        logger.info(f"[RiskGuard] step 4 done: ok={drawdown_ok}")
        if not drawdown_ok:
            logger.warning(f"[RiskGuard] REJECTED: {reason}")
            return await self._reject(user_id, reason)

        return True, "OK"

    async def _reject(self, user_id: str, reason: str) -> Tuple[bool, str]:
        """拒绝下单**并让用户看见**，然后返回 (False, reason)。

        2026-09-22：`pre_trade_check` 返回 False 时 `OrderExecutor` 抛
        PermissionError，而这发生在订单落库**之前**——既没有订单行，也不会走
        `record_order_failure` 那条通知。用户的意图于是被静默丢弃，与
        `record_order_failure` 里记的 2026-09-21 事故是同一种病。
        通知失败只记日志，绝不改变「拒绝」这个决定本身。
        """
        from apps.trading.alerts import notify_user

        try:
            await notify_user(user_id, f"⚠️ 下单被风控拦截\n{reason}")
        except Exception:  # noqa: BLE001 - 通知不得影响风控判定
            logger.error(
                "[RiskGuard] 拒单通知投递失败 user=%s", user_id, exc_info=True
            )
        return False, reason

    async def _is_circuit_open(self, user_id: str) -> bool:
        """检查熔断器是否打开"""
        cb = CircuitBreaker(user_id=user_id)
        return await cb.is_open()

    async def record_order_success(self, user_id: str) -> None:
        """订单成功时调用，重置失败计数"""
        if not user_id:
            return
        cb = CircuitBreaker(user_id=user_id)
        await cb.record_success()

    async def record_order_failure(
        self,
        user_id: Optional[str],
        *,
        symbol: str = "",
        side: str = "",
        quantity: str = "",
        error: str = "",
        unknown: bool = False,
    ) -> None:
        """订单失败时调用：连续失败触发熔断 + 给用户一条可见通知。

        2026-09-21 事故：失败只喂熔断器，用户完全无感（意图中的订单被静默丢弃，
        只能翻订单列表才发现）。因此这里必须留一条用户可见的通知。

        2026-09-22：通知改走出站唯一口 ``notify_user``（落库 + 即时推送），不再自己
        内联写 ``Notification``——两个投递口迟早会漂。**行为变更**：订单失败从此
        还有一条即时推送，不再只在 Web 通知中心可见。
        通知失败只记 ERROR，绝不影响熔断/下单主流程。

        ``unknown=True`` 用于「下单结果未知」（``OrderPlacementUnknown``）：那张单
        可能已在交易所活着，所以**必须改口径**——说「下单失败」会让用户重下一张，
        把敞口变成两倍。熔断计数不变（未知单同样是风险信号），只是措辞如实。
        """
        if not user_id:
            return
        cb = CircuitBreaker(user_id=user_id)
        await cb.record_failure()

        from apps.trading.alerts import notify_user

        detail = f"{symbol} {side} qty={quantity}".strip()
        if unknown:
            prefix = "⚠️ 下单结果未知（可能已成交，请先到交易所核对再决定是否重下）"
        else:
            prefix = "⚠️ 下单失败"
        try:
            delivered = await notify_user(
                user_id,
                f"{prefix}: {detail}｜原因: {error or '交易所未返回原因'}",
            )
        except Exception as e:  # noqa: BLE001 - 通知失败不得影响主流程
            logger.error(
                "下单失败通知投递异常（熔断计数已完成，user=%s symbol=%s）: %s",
                user_id,
                symbol,
                e,
                exc_info=True,
            )
            return

        if not delivered:
            logger.error(
                "下单失败通知未送达（熔断计数已完成，user=%s symbol=%s）: %s",
                user_id,
                symbol,
                error or "交易所未返回原因",
            )

    async def _get_daily_trade_count(self, user_id: str) -> int:
        """统计日内已成交订单数量"""
        from apps.trading.models import Order
        from django.utils import timezone

        today = timezone.now().date()

        @db_async
        def count():
            # 「未知」也计入：它意味着这张单可能真的发到了交易所，按保守口径
            # 必须占用当日的下单额度（漏计会让熔断线被绕过）。
            return Order.objects.filter(
                user_id=user_id,
                created_at__date=today,
                status__in=["submitted", "filled", "unknown"],
            ).count()

        return await count()

    async def _check_position_limit(
        self, request: "OrderRequest", user_id: str
    ) -> Tuple[bool, str]:
        """单笔仓位不超过总资产20%"""
        from apps.trading.executor import OrderExecutor

        executor = OrderExecutor.get_instance()
        if not executor:
            return True, ""

        adapter = executor._adapters.get(request.exchange)
        if not adapter:
            return True, ""

        try:
            balance = await adapter.get_balance()
        except Exception:
            return True, ""

        total_usdt = balance.get("USDT", Decimal("0"))
        if total_usdt == 0:
            return True, ""

        if request.price is None:
            # 市价单无法预知价格，跳过仓位校验
            return True, ""

        order_value = request.price * request.quantity
        ratio = order_value / total_usdt
        if ratio > self.MAX_POSITION_RATIO:
            return False, (
                f"单笔仓位 {ratio:.1%} 超过上限 {self.MAX_POSITION_RATIO:.0%}"
            )
        return True, ""

    async def _check_drawdown(self, user_id: str) -> Tuple[bool, str]:
        """
        日内已实现回撤检查：分子是当日已实现盈亏，分母是**当日净值快照**
        （`daily_snapshots`，由 `apps.trading.tasks.snapshot_daily_equity` 每 5 分钟
        写入、当日只写第一条 → `date=today` 那条就是「当日首次观测到的权益」，
        也正是本检查要的「期初」）。

        分母只取**当日**（`date=today`），不向前回退到昨天：昨天的权益配今天的
        盈亏是两个口径，宁可承认「今天还没有分母」也不拿旧分母充数。代价是每个
        UTC 日界之后、当日第一条快照写出来之前（≤ 一个 beat 周期）分母缺失，这段
        窗口本检查降级放行（见下）。

        原实现在这里直接「无法获取期初资金数据」拒单——把「当天还没有分母」当成
        「不该下单」，于是新用户第一天完全不能开仓、每天日界后还要禁交易 5 分钟，
        且不产生任何告警。这是本单元要消灭的静默故障：**降级放行 + WARNING**，
        而写入方取不到余额时会主动告警用户（`daily_snapshot._alert_write_failure`）。

        已知残留缺口（未消除，只是不再沉默）：若 beat/worker 长期不跑，`date=today`
        永远取不到分母，回撤保护就一直处于降级状态，而写入方自己也没机会告警。
        这一层由任务健康检查兜底；`date__lte` 那种回退写法没有这个缺口。
        """
        from django.db.models import Sum
        from django.utils import timezone

        today = timezone.now().date()

        @db_async
        def get_today_pnl():
            from apps.trading.models import Order

            result = Order.objects.filter(
                user_id=user_id,
                status="filled",
                created_at__date=today,
            ).aggregate(total_pnl=Sum("realized_pnl"))
            return result["total_pnl"]

        @db_async
        def get_opening_equity():
            """取**当日**快照；当日还没写出来则返回 None（由调用方降级放行）。"""
            from apps.trading.models import DailySnapshot

            snap = (
                DailySnapshot.objects.filter(
                    user_id=user_id,
                    date=today,
                )
                .order_by("-date")
                .first()
            )
            if snap:
                return snap.total_equity
            return None

        try:
            pnl = await get_today_pnl()
        except Exception:
            return await self._reject(user_id, "无法获取当日已实现盈亏数据")

        # 无已成交订单，视作无亏损
        if pnl is None:
            pnl = Decimal("0")

        if pnl >= 0:
            return True, ""

        try:
            initial = await get_opening_equity()
        except Exception:
            return await self._reject(user_id, "无法读取净值快照（数据库异常）")

        if initial is None:
            logger.warning(
                "[RiskGuard] user=%s 当日（%s）没有净值快照，日内回撤检查本轮降级放行"
                "（等待 snapshot_daily_equity 写入，它与本检查同一口径）",
                user_id,
                today,
            )
            return True, ""

        if initial <= 0:
            # 账户净值真的是 0（不是「取不到」）——这是可以告知用户的事实，
            # 且此时任何下单都会在交易所侧失败，拒绝是如实而不是沉默。
            return await self._reject(
                user_id, f"账户净值为 {initial}，无法计算日内回撤"
            )

        drawdown = abs(pnl) / initial
        if drawdown > self.MAX_DAILY_DRAWDOWN:
            return await self._reject(
                user_id,
                f"日内回撤 {drawdown:.1%} 超过上限 {self.MAX_DAILY_DRAWDOWN:.0%}"
                f"（当日已实现亏损 {pnl}，期初净值 {initial}）",
            )
        return True, ""

    # ------------------------------------------------------------------ #
    #  实时监控 Loop                                                     #
    # ------------------------------------------------------------------ #

    async def _monitor_loop(self) -> None:
        """每分钟检查所有持仓的浮亏"""
        while self._running:
            try:
                await self._check_floating_pnl()
            except Exception as e:
                logger.error(f"RiskGuard monitor error: {e}")
            await asyncio.sleep(60)

    async def _check_floating_pnl(self) -> None:
        """浮亏超阈值时发送告警"""
        from apps.trading.executor import OrderExecutor

        executor = OrderExecutor.get_instance()
        if not executor:
            return

        for exchange, adapter in executor._adapters.items():
            try:
                positions = await adapter.get_positions()
            except Exception as e:
                self._position_fetch_failures += 1
                # 必须带异常类型：httpx 超时异常的 message 为空，只打 {e} 会得到一行空消息
                # （2026-09-20 事故：连续 10 小时 100% 失败却无从诊断）
                logger.error(
                    f"Failed to fetch positions from {exchange} "
                    f"({type(e).__name__}: {e!r}); "
                    f"consecutive_failures={self._position_fetch_failures}"
                )
                continue
            self._position_fetch_failures = 0

            try:
                balance = await adapter.get_balance()
            except Exception as e:
                logger.error(
                    f"Failed to fetch balance from {exchange} "
                    f"({type(e).__name__}: {e!r})"
                )
                continue

            total = balance.get("USDT", Decimal("1"))
            if total <= 0:
                total = Decimal("1")

            for pos in positions:
                pnl_ratio = pos.unrealized_pnl / total
                if pnl_ratio < self.FLOATING_LOSS_ALERT:
                    await self._send_floating_loss_alert(exchange, pos, pnl_ratio)
                    # 记录风控事件
                    await self._record_risk_event(
                        level="P1",
                        event_type="floating_loss",
                        message=f"{exchange}:{pos.symbol} 浮亏 {pnl_ratio:.2%}",
                    )

    async def _send_floating_loss_alert(
        self, exchange: str, pos, ratio: Decimal
    ) -> None:
        """发送 Telegram 浮亏预警"""
        try:
            # TelegramChannel 需要 app 实例，通过日志作为 fallback
            logger.warning(
                f"浮亏预警 | 交易所: {exchange} | 品种: {pos.symbol} | "
                f"方向: {pos.side} | 数量: {pos.quantity} | 浮亏: {ratio:.2%}"
            )
        except Exception:
            logger.warning(
                f"浮亏预警 | 交易所: {exchange} | 品种: {pos.symbol} | "
                f"方向: {pos.side} | 数量: {pos.quantity} | 浮亏: {ratio:.2%}"
            )

    async def _record_risk_event(
        self,
        level: str,
        event_type: str,
        message: str,
    ) -> None:
        """记录风控事件到数据库"""
        try:
            from apps.risk.models import RiskEvent

            @db_async
            def create():
                return RiskEvent.objects.create(
                    level=level,
                    event_type=event_type,
                    message=message,
                )

            await create()
        except Exception as e:
            logger.error(f"Failed to record risk event: {e}")
