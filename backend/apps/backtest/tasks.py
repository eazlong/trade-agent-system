"""
Celery tasks for backtest execution.

异步回测任务，供 BacktestAgent 和 API 调用。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, max_retries=2, acks_late=True, track_started=True)
def run_backtest_task(
    self,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    initial_capital: float = 10000,
    commission_rate: float = 0.001,
    parameters: dict | None = None,
    strategy_id: str | None = None,
    exchange: str = "binance",
    start_date: str = "",
    end_date: str = "",
    user_id: str | None = None,
    result_id: str | None = None,
    benchmark: str = "",
) -> dict:
    """
    异步执行策略回测。

    Args:
        strategy_name: 策略名称（StrategyRegistry 注册名）
        symbol: 交易对
        timeframe: K 线周期
        initial_capital: 初始资金
        commission_rate: 手续费率
        parameters: 策略参数
        strategy_id: Strategy 模型 UUID（用于关联回测结果）
        exchange: 交易所
        start_date: 回测开始日期（ISO 格式，如 2024-01-01），空字符串表示不限
        end_date: 回测结束日期（ISO 格式，如 2024-12-31），空字符串表示不限
        user_id: 发起回测的用户 ID
        result_id: BacktestResult UUID（如果已提前创建占位记录）

    Returns:
        回测统计结果
    """
    from django.db import close_old_connections

    from apps.strategy_engine.backtest_mode import _resolve_strategy_name
    from apps.strategy_engine.registry import StrategyRegistry
    from apps.strategy_engine.runner import StrategyRunner
    from apps.agent.task_tracker import TaskTracker, tracker_context

    tracker = TaskTracker(
        task_id=self.request.id, user_id=str(user_id or ""), task_type="backtest"
    )
    token = tracker_context.set(tracker)
    tracker.start(
        f"开始回测：{strategy_name} {symbol} {timeframe}",
    )

    # ── Persist celery_task_id → BacktestResult mapping so get_task_result
    #     can find the result even after Redis/Celery backends expire ──
    if result_id:
        try:
            close_old_connections()
            from apps.backtest.models import BacktestResult as BR
            BR.objects.filter(id=result_id).update(
                celery_task_id=self.request.id
            )
        except Exception:
            logger.warning(
                "[BacktestTask] failed to set celery_task_id on result %s",
                result_id, exc_info=True,
            )

    try:
        logger.info(
            f"[BacktestTask] running: strategy={strategy_name} symbol={symbol} "
            f"tf={timeframe} exchange={exchange}"
        )

        if not StrategyRegistry._strategy_path:
            StrategyRegistry.set_strategy_path('/root/.tradelogx/strategies')

        StrategyRegistry.discover()
        strategy_name = _resolve_strategy_name(strategy_name)

        if not result_id:
            try:
                from apps.strategy_engine.backtest_mode import create_empty_result

                # Use sync ORM directly — _aio.run(sync_to_async()) fails in Celery
                # because Django's async context isn't initialized
                close_old_connections()
                from apps.trading.models import Strategy

                canonical = strategy_name  # already resolved above
                _sid_obj, _ = Strategy.objects.get_or_create(
                    name=canonical,
                    defaults={
                        "code_path": f"strategies/{canonical}.py",
                        "is_active": False,
                    },
                )
                _sid = str(_sid_obj.id)
                strategy_id = _sid
                if _sid:
                    _s = start_date or (date.today() - timedelta(days=30)).isoformat()
                    _e = end_date or date.today().isoformat()
                    result_id = create_empty_result(
                        strategy_id=_sid,
                        symbol=symbol,
                        timeframe=timeframe,
                        start_date=_s,
                        end_date=_e,
                        initial_capital=initial_capital,
                        parameters=parameters or {},
                        user_id=user_id,
                    )
                    logger.info(
                        f"[BacktestTask] created placeholder result_id=%s", result_id
                    )
            except Exception:
                logger.warning(
                    "[BacktestTask] failed to create placeholder result", exc_info=True
                )

        self.update_state(
            state="STARTED", meta={"step": "fetching_ohlcv", "symbol": symbol}
        )
        tracker.milestone("正在获取历史K线数据...", progress=0.1)
        ohlcv_data = _fetch_ohlcv_sync(
            symbol, timeframe, exchange, start_date=start_date, end_date=end_date
        )
        if not ohlcv_data:
            tracker.fail(f"未能获取 {symbol} {timeframe} 的历史K线数据")
            raise ValueError(f"未能获取 {symbol} {timeframe} 的历史K线数据")

        logger.info(f"[BacktestTask] OHLCV data fetched: {len(ohlcv_data)} bars")
        tracker.milestone(
            f"已获取 {len(ohlcv_data)} 条K线数据，正在执行回测...", progress=0.3
        )

        self.update_state(
            state="STARTED",
            meta={"step": "running_backtest", "bars": len(ohlcv_data)},
        )

        # DB connections may have gone stale during _fetch_ohlcv_sync
        close_old_connections()

        runner = StrategyRunner()

        loop = asyncio.new_event_loop()
        try:
            stats = loop.run_until_complete(
                runner.run_backtest(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    timeframe=timeframe,
                    ohlcv_data=ohlcv_data,
                    initial_capital=Decimal(str(initial_capital)),
                    parameters=parameters,
                    strategy_id=strategy_id,
                    commission_rate=Decimal(str(commission_rate)),
                    result_id=result_id,
                    benchmark=benchmark,
                )
            )
        finally:
            _close_async_resources(loop)
            loop.close()

        total_trades = stats.get("total_trades", 0)
        total_return = stats.get("total_return_pct", 0)
        sharpe = stats.get("sharpe_ratio", 0) or 0
        win_rate = stats.get("win_rate", 0) or 0

        logger.info(
            f"[BacktestTask] backtest complete: result_id={stats.get('result_id')} "
            f"trades={total_trades}"
        )
        tracker.complete(
            f"{strategy_name} {symbol} {timeframe} 回测完成\n"
            f"交易 {total_trades} 笔 | "
            f"收益率 {total_return:.2f}% | "
            f"夏普 {sharpe:.2f} | "
            f"胜率 {win_rate:.1%}"
        )
        return stats
    except Exception as e:
        tracker.fail(f"回测失败: {strategy_name} {symbol} {timeframe}: {str(e)}")
        raise
    finally:
        tracker.stop()
        tracker_context.reset(token)


def _fetch_ohlcv_sync(
    symbol: str,
    timeframe: str,
    exchange: str = "binance",
    limit: int = 1000,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    """同步获取历史 OHLCV 数据（供 Celery task 使用）"""
    import ccxt.async_support as ccxt
    from django.conf import settings

    symbol_normalized = symbol.replace("-", "/").replace("_", "/")
    if "/" not in symbol_normalized:
        symbol_normalized = f"{symbol_normalized}/USDT"

    since_ms: int | None = None
    end_ms: int | None = None

    if start_date:
        try:
            since_ms = int(datetime.fromisoformat(start_date).timestamp() * 1000)
        except (ValueError, TypeError):
            logger.warning(f"[BacktestTask] invalid start_date={start_date}, ignoring")
    if end_date:
        try:
            end_ms = int(datetime.fromisoformat(end_date).timestamp() * 1000)
        except (ValueError, TypeError):
            logger.warning(f"[BacktestTask] invalid end_date={end_date}, ignoring")

    async def _fetch():
        exchange_class = getattr(ccxt, exchange.lower(), None)
        if exchange_class is None:
            logger.error(f"[BacktestTask] unsupported exchange: {exchange}")
            return []

        options = {"enableRateLimit": True}
        proxy = getattr(settings, "WEB_PROXY", "") or None
        if proxy:
            options["aiohttp_proxy"] = proxy

        ex = exchange_class(options)
        try:
            # 分页获取全部历史数据（CCXT 每次最多返回 limit 根）
            all_candles: list = []
            batch_since = since_ms
            while True:
                ohlcv = await ex.fetch_ohlcv(
                    symbol_normalized, timeframe, since=batch_since, limit=limit
                )
                if not ohlcv:
                    break
                raw_count = len(ohlcv)
                # Filter batch by end_ms to avoid fetching beyond the target range
                if end_ms is not None:
                    ohlcv = [c for c in ohlcv if c[0] <= end_ms]
                all_candles.extend(ohlcv)
                if raw_count < limit:
                    # Reached present (exchange had fewer bars than requested)
                    break
                if not ohlcv:
                    # All bars in this batch were beyond end_ms
                    break
                # 下一批从最后一根 K 线的时间戳+1ms 开始
                batch_since = ohlcv[-1][0] + 1
                if end_ms is not None and batch_since > end_ms:
                    break
        finally:
            await ex.close()

        if not all_candles:
            logger.warning(f"[BacktestTask] no OHLCV data for {symbol_normalized}")
            return []

        result = []
        for candle in all_candles:
            ts_ms = int(candle[0])
            if end_ms is not None and ts_ms > end_ms:
                continue
            result.append(
                {
                    "timestamp": datetime.fromtimestamp(ts_ms / 1000).isoformat(),
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]),
                }
            )

        return result

    try:
        return asyncio.run(_fetch())
    except Exception as e:
        logger.error(f"[BacktestTask] OHLCV fetch failed: {e}", exc_info=True)
        return []


def _close_async_resources(loop: asyncio.AbstractEventLoop) -> None:
    """显式关闭 Redis 连接等异步资源，防止 __del__ 在 loop 关闭后报错。"""
    try:
        from apps.memory.redis_client import RedisPool

        client = RedisPool.get_client()

        async def _aclose():
            await client.aclose()

        loop.run_until_complete(_aclose())
        RedisPool.close_client()
    except Exception:
        pass


@app.task(
    bind=True,
    max_retries=1,
    acks_late=True,
    track_started=True,
    soft_time_limit=1800,
    time_limit=3600,
    queue='grid_search',
)
def run_grid_search_task(self, job_id: str, user_id: str | None = None) -> dict:
    """
    执行网格搜索任务，批量回测所有参数组合。

    Args:
        job_id: GridSearchJob UUID
        user_id: 发起任务的用户 ID（定时任务时可能为 None）

    Returns:
        {"job_id": str, "total": int, "completed": int, "best_result_id": str}
    """
    from celery.exceptions import SoftTimeLimitExceeded
    from django.utils import timezone
    from django.db import close_old_connections

    from apps.backtest.models import BacktestResult, GridSearchJob
    from apps.agent.task_tracker import TaskTracker, tracker_context
    from apps.strategy_engine.grid_search import generate_combinations
    from apps.strategy_engine.runner import StrategyRunner

    try:
        job = GridSearchJob.objects.select_related("strategy").get(id=job_id)
    except GridSearchJob.DoesNotExist:
        logger.error(f"[GridSearchTask] job {job_id} not found")
        return {
            "status": "FAILURE",
            "error_type": "NotFoundError",
            "error_message": "job not found",
            "error": "job not found",
        }

    # Fall back to job's user_id if not provided (e.g. called via Agent tool)
    if not user_id and job.user_id:
        user_id = str(job.user_id)

    if user_id and str(job.user_id) != user_id:
        logger.error(
            f"[GridSearchTask] user mismatch: job={job.user_id} requested={user_id}"
        )
        return {
            "status": "FAILURE",
            "error_type": "PermissionError",
            "error_message": "user mismatch",
            "error": "user mismatch",
        }

    tracker = TaskTracker(
        task_id=self.request.id, user_id=str(user_id or ""), task_type="grid_search"
    )
    token = tracker_context.set(tracker)
    tracker.start(f"开始网格搜索：{job.symbol} {job.timeframe}")

    try:
        job.status = "running"
        job.celery_task_id = self.request.id
        job.save(update_fields=["status", "celery_task_id"])

        config = job.search_config
        strategy_name = job.strategy.name
        combinations = generate_combinations(config)
        job.total_combinations = len(combinations)
        job.save(update_fields=["total_combinations"])

        tracker.milestone(
            f"已生成 {len(combinations)} 个参数组合", progress=0.05
        )

        tracker.milestone("正在获取历史K线数据...", progress=0.1)
        ohlcv_data = _fetch_ohlcv_sync(
            job.symbol,
            job.timeframe,
            exchange="binance",
            start_date=job.start_date.isoformat() if isinstance(job.start_date, date) else str(job.start_date),
            end_date=job.end_date.isoformat() if isinstance(job.end_date, date) else str(job.end_date),
        )
        if not ohlcv_data:
            tracker.fail(f"未能获取 {job.symbol} {job.timeframe} 的历史K线数据")
            job.status = "failed"
            job.error_log = [{"error": "failed to fetch OHLCV data"}]
            job.save(update_fields=["status", "error_log"])
            return {
                "status": "FAILURE",
                "error_type": "DataError",
                "error_message": "OHLCV fetch failed",
                "error": "OHLCV fetch failed",
            }

        tracker.milestone(f"已获取 {len(ohlcv_data)} 条K线数据", progress=0.2)

        runner = StrategyRunner()
        results = []
        loop = asyncio.new_event_loop()
        start_time = timezone.now().timestamp()

        try:
            for idx, params in enumerate(combinations):
                # DB 连接在长时间的 OHLCV 抓取 / 上一个组合回测期间可能被
                # pgbouncer 关闭（CONN_MAX_AGE=30s）。每个组合开始前刷新连接，
                # 否则 save_backtest_result 会因 "connection already closed" 失败，
                # 导致整个网格搜索 0 条结果落库（前端因此看不到任何子回测）。
                close_old_connections()
                try:
                    job.refresh_from_db()
                    if job.status == "cancelled":
                        tracker.milestone("任务已被取消", progress=0.0)
                        return {
                            "status": "CANCELLED",
                            "cancelled": True,
                            "completed": idx,
                            "message": f"任务已被取消，已完成 {idx} 个组合",
                        }
                except Exception:
                    pass

                combo_label = ", ".join(f"{k}={v}" for k, v in params.items())
                progress = 0.2 + 0.7 * ((idx + 1) / max(len(combinations), 1))
                tracker.milestone(
                    f"执行组合 [{idx + 1}/{len(combinations)}]: {combo_label}",
                    progress=progress,
                )

                try:
                    stats = loop.run_until_complete(
                        runner.run_backtest(
                            strategy_name=strategy_name,
                            symbol=job.symbol,
                            timeframe=job.timeframe,
                            ohlcv_data=ohlcv_data,
                            initial_capital=Decimal(str(job.initial_capital)),
                            parameters=params,
                            strategy_id=str(job.strategy_id),
                            commission_rate=Decimal(str(job.commission_rate)),
                            benchmark="",
                            user_id=str(job.user_id) if job.user_id else None,
                        )
                    )
                    # run_backtest 可能耗时较长，期间 DB 连接可能被 pgbouncer 关闭，
                    # 需要在 ORM 操作前刷新连接，避免 "connection already closed"
                    close_old_connections()
                    result_id = stats.get("result_id")
                    if result_id:
                        BacktestResult.objects.filter(id=result_id).update(
                            grid_search_id=job.id, is_grid_search=True
                        )
                        results.append(
                            {"result_id": result_id, "params": params, **stats}
                        )
                except SoftTimeLimitExceeded:
                    raise
                except Exception as e:
                    close_old_connections()
                    job.refresh_from_db()
                    job.error_log = job.error_log + [
                        {"combination_index": idx, "params": params, "error": str(e)}
                    ]
                    job.save(update_fields=["error_log"])
                    logger.warning(
                        f"[GridSearchTask] combination {idx} failed: {e}"
                    )

                job.completed_combinations = idx + 1
                job.save(update_fields=["completed_combinations"])
        finally:
            _close_async_resources(loop)
            loop.close()

        # 排序并标记最优结果
        sort_key = job.sort_by or "sharpe_ratio"
        if results:
            sorted_results = sorted(
                results,
                key=lambda r: r.get(sort_key, 0) or 0,
                reverse=True,
            )
            best = sorted_results[0]
            best_result_id = best.get("result_id")
            if best_result_id:
                try:
                    best_result_obj = BacktestResult.objects.get(id=best_result_id)
                    job.best_result = best_result_obj
                except BacktestResult.DoesNotExist:
                    pass

        elapsed = int(timezone.now().timestamp() - start_time)
        best_val = results[0].get(sort_key) if results else "N/A"
        job.status = "completed"
        job.save(update_fields=["status", "completed_combinations", "best_result"])

        tracker.complete(
            f"网格搜索完成：{len(results)}/{job.total_combinations}，"
            f"耗时 {elapsed}s，最优 {sort_key}={best_val}"
        )

        return {
            "job_id": str(job.id),
            "total_combinations": job.total_combinations,
            "completed_combinations": job.completed_combinations,
            "best_result_id": str(job.best_result_id) if job.best_result else None,
        }

    except SoftTimeLimitExceeded:
        job.status = "failed"
        job.error_log = job.error_log + [{"error": "soft time limit exceeded"}]
        job.save(update_fields=["status", "error_log"])
        tracker.fail("网格搜索超时")
        return {
            "status": "FAILURE",
            "error_type": "TimeoutError",
            "error_message": "time limit exceeded",
            "error": "time limit exceeded",
        }
    except Exception as e:
        job.status = "failed"
        job.error_log = job.error_log + [{"error": str(e)}]
        job.save(update_fields=["status", "error_log"])
        tracker.fail(f"网格搜索失败: {e}")
        raise
    finally:
        tracker.stop()
        tracker_context.reset(token)

