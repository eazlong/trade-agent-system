"""
策略运行器 — 统一入口

管理策略在回测/实盘模式下的生命周期。
调用方无需关心底层是回测还是实盘，只需调用对应方法。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)


class StrategyRunner:
    """策略运行器：回测/实盘统一入口"""

    def __init__(self):
        self._strategy: BaseStrategy | None = None
        self._context: StrategyContext | None = None
        self._live_runner = None

    # ─── 回测模式 ────────────────────────────────────────────────────────────

    async def run_backtest(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        ohlcv_data: list[dict],
        initial_capital: Decimal,
        parameters: dict | None = None,
        strategy_id: str | None = None,
        git_commit_hash: str = "",
        commission_rate: Decimal = Decimal("0.001"),
    ) -> dict:
        """
        用历史数据回放策略。

        Args:
            strategy_name: 策略名称（StrategyRegistry 中注册的 name）
            symbol: 交易对
            timeframe: K 线周期
            ohlcv_data: 历史 K 线数据 [{open, high, low, close, volume, timestamp}, ...]
            initial_capital: 初始资金
            parameters: 策略参数（覆盖 defaults）
            strategy_id: Strategy 模型 UUID（用于存回测结果）
            git_commit_hash: 策略代码的 git 提交哈希
            commission_rate: 手续费率

        Returns:
            回测统计结果
        """
        from .registry import StrategyRegistry
        from .backtest_mode import BacktestEngine, save_backtest_result

        # 加载策略类
        strategy_cls = StrategyRegistry.get_class(strategy_name)

        # 合并参数
        params = {**getattr(strategy_cls, "params_schema", {})}
        if parameters:
            params.update(parameters)

        # 创建上下文
        context = StrategyContext(
            symbol=symbol,
            timeframe=timeframe,
            mode="backtest",
            params=params,
            balance=initial_capital,
            position=Decimal("0"),
        )

        # 实例化策略
        strategy = strategy_cls(context)
        logger.info(
            f"[StrategyRunner] backtest starting: {strategy_name} "
            f"{symbol} {timeframe} bars={len(ohlcv_data)}"
        )

        # 运行回测
        engine = BacktestEngine(
            strategy=strategy,
            ohlcv_data=ohlcv_data,
            initial_capital=initial_capital,
            symbol=symbol,
            timeframe=timeframe,
            commission_rate=commission_rate,
        )
        stats = await engine.run()

        # 存入数据库
        if strategy_id:
            start_date = ohlcv_data[0].get("timestamp", "")[:10]
            end_date = ohlcv_data[-1].get("timestamp", "")[:10]
            result = await save_backtest_result(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                parameters=parameters or {},
                stats=stats,
                ohlcv_data=ohlcv_data[:500],
                git_commit_hash=git_commit_hash,
            )
            stats["result_id"] = result["result_id"]
            logger.info(
                f"[StrategyRunner] backtest result saved: {result['result_id']} "
                f"trades={result['trade_count']}"
            )

        return stats

    # ─── 实盘模式 ────────────────────────────────────────────────────────────

    async def start_live(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        parameters: dict | None = None,
        exchange_account_id: str = "",
        user_id: str | None = None,
        live_session_id: str | None = None,
        initial_balance: Decimal = Decimal("0"),
    ) -> None:
        """
        启动实盘策略运行。

        Args:
            strategy_name: 策略名称
            symbol: 交易对
            timeframe: K 线周期
            parameters: 策略参数
            exchange_account_id: 交易所账户 UUID
            user_id: 用户 UUID
            live_session_id: 实盘会话 UUID
            initial_balance: 初始余额
        """
        from .registry import StrategyRegistry
        from .live_mode import LiveStrategyRunner

        # 加载策略类
        strategy_cls = StrategyRegistry.get_class(strategy_name)

        # 合并参数
        params = {}
        if parameters:
            params.update(parameters)

        # 创建上下文
        context = StrategyContext(
            symbol=symbol,
            timeframe=timeframe,
            mode="live",
            params=params,
            balance=initial_balance,
            position=Decimal("0"),
        )

        # 实例化策略
        strategy = strategy_cls(context)
        logger.info(
            f"[StrategyRunner] live starting: {strategy_name} {symbol} {timeframe}"
        )

        # 创建实盘运行器
        self._live_runner = LiveStrategyRunner(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            exchange_account_id=exchange_account_id,
            user_id=user_id,
            live_session_id=live_session_id,
        )

        # 加载初始历史
        await self._live_runner.load_initial_history()

        # 启动
        await self._live_runner.start()

    async def stop_live(self) -> None:
        """停止实盘策略运行"""
        if self._live_runner:
            await self._live_runner.stop()
            self._live_runner = None
            logger.info("[StrategyRunner] live strategy stopped")

    async def on_kline(self, kline: dict) -> None:
        """
        外部 K 线数据注入入口。
        供 FrameManager 或 DataFeed 回调使用。
        """
        if self._live_runner:
            await self._live_runner.on_kline(kline)

    @property
    def is_running_live(self) -> bool:
        """是否正在运行实盘策略"""
        return self._live_runner is not None and self._live_runner._running
