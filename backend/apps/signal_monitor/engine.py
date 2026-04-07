"""
信号监控引擎

核心功能：
- 加载活跃监控列表
- 并行计算技术指标
- 评估触发条件
- 执行预设动作（通知/交易）
- 区分单次触发和持续触发
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np
from django.utils import timezone

from .indicators import compute_indicator, compute_indicators_parallel
from .conditions import evaluate_condition
from .models import SignalMonitor, SignalTriggerLog

logger = logging.getLogger(__name__)


class SignalMonitorEngine:
    """信��监控引擎"""

    _instance: SignalMonitorEngine | None = None

    def __init__(self):
        self._prev_results: dict[str, Any] = {}

    @classmethod
    def get_instance(cls) -> SignalMonitorEngine:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def check_all_signals(self, klines_map: dict[str, list[dict]] | None = None) -> list[dict]:
        """
        检查所有活跃信号。

        Args:
            klines_map: 外部传入的 K 线数据 {symbol: [klines]}
                       如果为 None，从 MemoryDataStore 获取。

        Returns:
            触发结果列表
        """
        start = time.monotonic()

        # 加载活跃监控
        monitors = list(
            SignalMonitor.objects.filter(status='active').select_related('user')
        )
        if not monitors:
            return []

        # 过滤过期的
        now = timezone.now()
        active_monitors = []
        for m in monitors:
            if m.expires_at and m.expires_at < now:
                m.status = 'expired'
                m.save(update_fields=['status', 'updated_at'])
                continue
            active_monitors.append(m)

        if not active_monitors:
            return []

        # 获取 K 线数据
        if klines_map is None:
            klines_map = self._load_klines_for_monitors(active_monitors)

        # 构建计算任务
        tasks = []
        for monitor in active_monitors:
            symbol = monitor.symbol
            klines = klines_map.get(symbol, [])
            if len(klines) < 2:
                continue
            tasks.append({
                'monitor': monitor,
                'indicator_type': monitor.indicator_type,
                'klines': klines,
                'params': monitor.indicator_params,
            })

        if not tasks:
            return []

        # 并行计算指标
        compute_tasks = [
            {
                'indicator_type': t['indicator_type'],
                'klines': t['klines'],
                'params': t['params'],
            }
            for t in tasks
        ]
        compute_results = compute_indicators_parallel(compute_tasks)

        # 评估条件和执行动作
        trigger_results = []
        for task, compute_result in zip(tasks, compute_results):
            if compute_result['error']:
                logger.warning(
                    'Indicator computation failed for monitor %s: %s',
                    task['monitor'].id, compute_result['error'],
                )
                continue

            monitor = task['monitor']
            indicator_result = compute_result['result']
            prev_result = self._prev_results.get(str(monitor.id))

            # 评估条件
            triggered = evaluate_condition(
                monitor.condition,
                indicator_result,
                prev_result,
            )

            # 更新前一次结果
            self._prev_results[str(monitor.id)] = indicator_result

            if triggered:
                result = self._handle_trigger(monitor, indicator_result)
                trigger_results.append(result)

        duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            'Signal check completed: %d monitors, %d triggers, %.1fms',
            len(active_monitors), len(trigger_results), duration_ms,
        )

        return trigger_results

    def check_signals_for_kline(self, symbol: str, klines: list[dict]) -> list[dict]:
        """
        检查特定交易对的信号（用于 WebSocket 实时回调）。

        Args:
            symbol: 交易对
            klines: K线数据列表

        Returns:
            触发结果列表
        """
        now = timezone.now()
        monitors = list(
            SignalMonitor.objects.filter(
                symbol=symbol,
                status='active',
            ).select_related('user')
        )

        if not monitors:
            return []

        active_monitors = []
        for m in monitors:
            if m.expires_at and m.expires_at < now:
                m.status = 'expired'
                m.save(update_fields=['status', 'updated_at'])
                continue
            active_monitors.append(m)

        if not active_monitors:
            return []

        tasks = [
            {
                'indicator_type': m.indicator_type,
                'klines': klines,
                'params': m.indicator_params,
            }
            for m in active_monitors
        ]
        compute_results = compute_indicators_parallel(tasks)

        trigger_results = []
        for monitor, compute_result in zip(active_monitors, compute_results):
            if compute_result['error']:
                continue

            indicator_result = compute_result['result']
            prev_result = self._prev_results.get(str(monitor.id))

            triggered = evaluate_condition(
                monitor.condition, indicator_result, prev_result,
            )

            self._prev_results[str(monitor.id)] = indicator_result

            if triggered:
                result = self._handle_trigger(monitor, indicator_result)
                trigger_results.append(result)

        return trigger_results

    def _handle_trigger(self, monitor: SignalMonitor, indicator_result: Any) -> dict:
        """处理信号触发"""
        # 获取当前指标值用于日志
        from .conditions import _resolve_operand
        trigger_value = {}
        left_val = _resolve_operand(monitor.condition.get('left', {}), indicator_result)
        right_val = _resolve_operand(monitor.condition.get('right', {}), indicator_result)
        trigger_value = {
            'left': left_val,
            'right': right_val,
            'operator': monitor.condition.get('operator'),
        }

        # 执行动作
        action_result = self._execute_action(monitor, trigger_value)

        # 更新监控状态
        monitor.last_triggered_at = timezone.now()
        monitor.trigger_count += 1

        if monitor.trigger_type == 'once':
            monitor.status = 'triggered'

        monitor.save(update_fields=[
            'last_triggered_at', 'trigger_count', 'status', 'updated_at',
        ])

        # 记录日志
        SignalTriggerLog.objects.create(
            monitor=monitor,
            trigger_value=trigger_value,
            action_result=action_result,
        )

        return {
            'monitor_id': str(monitor.id),
            'monitor_name': monitor.name,
            'symbol': monitor.symbol,
            'indicator_type': monitor.indicator_type,
            'trigger_type': monitor.trigger_type,
            'trigger_value': trigger_value,
            'action_result': action_result,
        }

    def _execute_action(self, monitor: SignalMonitor, trigger_value: dict) -> dict:
        """执行预设动作"""
        action_type = monitor.action_type

        if action_type in ('notify', 'notify_and_trade'):
            self._send_notification(monitor, trigger_value)

        if action_type in ('trade', 'notify_and_trade'):
            self._execute_trade(monitor, trigger_value)

        return {
            'action_type': action_type,
            'status': 'executed',
        }

    def _send_notification(self, monitor: SignalMonitor, trigger_value: dict) -> None:
        """发送通知"""
        try:
            from apps.notify.models import Notification

            message = (
                f'信号触发: {monitor.name}\n'
                f'交易对: {monitor.symbol}\n'
                f'指标: {monitor.indicator_type}\n'
                f'条件: {monitor.condition.get("operator")} '
                f'{trigger_value.get("left")} vs {trigger_value.get("right")}\n'
                f'触发类型: {"单次" if monitor.trigger_type == "once" else "持续"}'
            )

            Notification.objects.create(
                user=monitor.user,
                channel='telegram',
                message=message,
            )
            logger.info('Notification sent for monitor %s', monitor.id)
        except Exception as e:
            logger.error('Failed to send notification: %s', e)

    def _execute_trade(self, monitor: SignalMonitor, trigger_value: dict) -> None:
        """执行交易"""
        try:
            from apps.trading.models import Order
            from apps.exchange.models import ExchangeAccount

            params = monitor.action_params
            exchange_account_id = params.get('exchange_account_id')

            if not exchange_account_id:
                logger.warning('No exchange_account_id in action_params for monitor %s', monitor.id)
                return

            exchange_account = ExchangeAccount.objects.get(id=exchange_account_id)

            Order.objects.create(
                user=monitor.user,
                exchange_account=exchange_account,
                symbol=monitor.symbol,
                side=params.get('side', 'buy'),
                order_type=params.get('order_type', 'market'),
                quantity=params.get('quantity', 0),
                price=params.get('price'),
            )
            logger.info('Trade order created for monitor %s', monitor.id)
        except Exception as e:
            logger.error('Failed to execute trade: %s', e)

    def _load_klines_for_monitors(self, monitors: list[SignalMonitor]) -> dict[str, list[dict]]:
        """从 MemoryDataStore 加载 K 线数据"""
        from apps.datasource.store import get_data_store

        store = get_data_store()
        klines_map: dict[str, list[dict]] = {}

        for monitor in monitors:
            if monitor.symbol not in klines_map:
                klines = store.get_latest('kline', monitor.symbol, limit=200)
                klines_map[monitor.symbol] = klines

        return klines_map
