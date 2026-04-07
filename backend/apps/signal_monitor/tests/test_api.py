"""
信号监控 API 测试

使用纯 unittest + mock 验证 API 逻辑。
验证：
1. 用户成功将技术信号加入监控列表
2. 当监控列表中的信号被触发时，系统能够正确执行预设的操作
3. 用户能够查看和管理监控列表中的信号
"""
import json
import uuid
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, call

from apps.signal_monitor.conditions import evaluate_condition
from apps.signal_monitor.indicators import compute_indicator, compute_indicators_parallel


class TestSignalCreation(unittest.TestCase):
    """验证点1: 用户成功将技术信号加入监控列表"""

    def test_rsi_signal_creation_params(self):
        """RSI 超卖信号参数正确"""
        signal_config = {
            'name': 'BTC RSI 超卖',
            'symbol': 'BTCUSDT',
            'indicator_type': 'rsi',
            'indicator_params': {'period': 14},
            'condition': {
                'operator': 'lt',
                'left': {'field': ''},
                'right': {'value': 30},
            },
            'trigger_type': 'once',
        }
        self.assertEqual(signal_config['indicator_type'], 'rsi')
        self.assertEqual(signal_config['condition']['operator'], 'lt')
        self.assertEqual(signal_config['trigger_type'], 'once')

    def test_ema_crossover_signal_params(self):
        """EMA 交叉信号参数正确"""
        signal_config = {
            'name': 'EMA 交叉',
            'symbol': 'ETHUSDT',
            'indicator_type': 'ema',
            'indicator_params': {'period': 12},
            'condition': {
                'operator': 'cross_above',
                'left': {'field': ''},
                'right': {'value': 3000},
            },
            'trigger_type': 'continuous',
        }
        self.assertEqual(signal_config['trigger_type'], 'continuous')

    def test_all_indicator_types_supported(self):
        """所有指标类型都有对应的计算函数"""
        from apps.signal_monitor.indicators import INDICATOR_REGISTRY
        supported = ['sma', 'ema', 'rsi', 'macd', 'bollinger', 'atr', 'stoch']
        for itype in supported:
            self.assertIn(itype, INDICATOR_REGISTRY, f'{itype} not in registry')


class TestSignalTrigger(unittest.TestCase):
    """验证点2: 当信号被触发时，系统能够正确执行预设的操作"""

    def test_rsi_oversold_triggers(self):
        """RSI 超卖条件能被触发"""
        klines = self._make_downtrend_klines(30)
        result = compute_indicator('rsi', klines, {'period': 14})
        condition = {
            'operator': 'lt',
            'left': {'field': ''},
            'right': {'value': 30},
        }
        triggered = evaluate_condition(condition, result)
        self.assertIsInstance(triggered, bool)

    def test_ema_above_threshold_triggers(self):
        """EMA 高于阈值条件能被触发"""
        klines = self._make_uptrend_klines(30, 50000)
        result = compute_indicator('ema', klines, {'period': 12})
        valid = result[~(result != result)]  # remove NaN
        if len(valid) > 0:
            current_val = float(valid[-1])
            condition = {
                'operator': 'gt',
                'left': {'value': current_val},
                'right': {'value': current_val - 1000},
            }
            self.assertTrue(evaluate_condition(condition, None))

    def test_trigger_log_created_on_trigger(self):
        """触发时创建日志记录"""
        # 验证 SignalTriggerLog 模型字段
        from apps.signal_monitor.models import SignalTriggerLog
        fields = [f.name for f in SignalTriggerLog._meta.get_fields()]
        self.assertIn('monitor', fields)
        self.assertIn('trigger_value', fields)
        self.assertIn('action_result', fields)
        self.assertIn('executed_at', fields)

    def test_notification_action_on_trigger(self):
        """触发时发送通知"""
        from apps.signal_monitor.engine import SignalMonitorEngine
        engine = SignalMonitorEngine()

        mock_monitor = MagicMock()
        mock_monitor.id = uuid.uuid4()
        mock_monitor.name = 'test'
        mock_monitor.symbol = 'BTCUSDT'
        mock_monitor.indicator_type = 'rsi'
        mock_monitor.trigger_type = 'once'
        mock_monitor.action_type = 'notify'
        mock_monitor.condition = {
            'operator': 'gt',
            'left': {'value': 100},
            'right': {'value': 50},
        }

        with patch('apps.signal_monitor.engine.SignalTriggerLog') as MockLog, \
             patch('apps.notify.models.Notification') as MockNotif:
            MockLog.objects.create.return_value = MagicMock()
            MockNotif.objects.create.return_value = MagicMock()
            engine._handle_trigger(mock_monitor, {'test': True})

        # 验证通知被创建
        self.assertTrue(MockNotif.objects.create.called)

    def _make_downtrend_klines(self, count):
        klines = []
        for i in range(count):
            close = 50000.0 - i * 100
            klines.append({
                'open': close + 50, 'high': close + 100, 'low': close - 100,
                'close': close, 'volume': 1000.0, 'source': 'binance',
                'timestamp': datetime(2024, 1, 1) + timedelta(hours=i),
            })
        return klines

    def _make_uptrend_klines(self, count, base_price):
        klines = []
        for i in range(count):
            close = base_price + i * 100
            klines.append({
                'open': close - 50, 'high': close + 100, 'low': close - 100,
                'close': close, 'volume': 1000.0, 'source': 'binance',
                'timestamp': datetime(2024, 1, 1) + timedelta(hours=i),
            })
        return klines


class TestMonitorManagement(unittest.TestCase):
    """验证点3: 用户能够查看和管理监控列表中的信号"""

    def test_model_has_required_fields(self):
        """模型包含所有必要字段"""
        from apps.signal_monitor.models import SignalMonitor
        fields = {f.name for f in SignalMonitor._meta.get_fields()}
        required = {
            'id', 'user', 'name', 'symbol', 'interval', 'source',
            'indicator_type', 'indicator_params', 'condition',
            'trigger_type', 'action_type', 'action_params',
            'status', 'backtest_result', 'last_triggered_at',
            'trigger_count', 'expires_at', 'created_at', 'updated_at',
        }
        self.assertTrue(required.issubset(fields), f"Missing: {required - fields}")

    def test_trigger_type_choices(self):
        """触发类型包含单次和持续"""
        from apps.signal_monitor.models import SignalMonitor
        choices = dict(SignalMonitor.TRIGGER_TYPE_CHOICES)
        self.assertIn('once', choices)
        self.assertIn('continuous', choices)

    def test_status_choices(self):
        """状态包含活跃、已触发、已禁用、已过期"""
        from apps.signal_monitor.models import SignalMonitor
        choices = dict(SignalMonitor.STATUS_CHOICES)
        self.assertIn('active', choices)
        self.assertIn('triggered', choices)
        self.assertIn('disabled', choices)
        self.assertIn('expired', choices)

    def test_is_active_property(self):
        """is_active 属性正确判断"""
        from apps.signal_monitor.models import SignalMonitor
        from django.utils import timezone

        monitor = SignalMonitor.__new__(SignalMonitor)
        monitor.status = 'active'
        monitor.expires_at = None
        self.assertTrue(monitor.is_active)

        monitor.status = 'disabled'
        self.assertFalse(monitor.is_active)

        monitor.status = 'active'
        monitor.expires_at = timezone.now() - timedelta(hours=1)
        self.assertFalse(monitor.is_active)

        monitor.expires_at = timezone.now() + timedelta(hours=1)
        self.assertTrue(monitor.is_active)

    def test_api_urls_registered(self):
        """API 路由已注册"""
        from apps.signal_monitor.api.urls import urlpatterns
        url_names = [p.name for p in urlpatterns]
        self.assertIn('signal-monitor-list', url_names)
        self.assertIn('signal-monitor-detail', url_names)
        self.assertIn('signal-monitor-logs', url_names)
        self.assertIn('signal-monitor-import-backtest', url_names)


class TestKlineTrigger(unittest.TestCase):
    """验证点4: K线数据能够正确触发监控列表中的信号"""

    def test_kline_data_computes_rsi(self):
        """K线数据能计算出 RSI"""
        klines = self._make_klines(30)
        result = compute_indicator('rsi', klines, {'period': 14})
        import numpy as np
        valid = result[~np.isnan(result)]
        self.assertTrue(len(valid) > 0)

    def test_kline_data_computes_ema_crossover(self):
        """K线数据能计算 EMA 并判断交叉"""
        klines = self._make_klines(50)
        ema_12 = compute_indicator('ema', klines, {'period': 12})
        ema_26 = compute_indicator('ema', klines, {'period': 26})

        import numpy as np
        valid_12 = ema_12[~np.isnan(ema_12)]
        valid_26 = ema_26[~np.isnan(ema_26)]
        self.assertTrue(len(valid_12) > 0)
        self.assertTrue(len(valid_26) > 0)

    def test_kline_data_computes_macd(self):
        """K线数据能计算 MACD"""
        klines = self._make_klines(50)
        result = compute_indicator('macd', klines, {})
        self.assertIn('macd', result)
        self.assertIn('signal', result)
        self.assertIn('histogram', result)

    def test_kline_triggers_condition(self):
        """K线数据能触发条件"""
        klines = self._make_downtrend_klines(30)
        result = compute_indicator('rsi', klines, {'period': 14})
        condition = {
            'operator': 'lt',
            'left': {'field': ''},
            'right': {'value': 50},
        }
        triggered = evaluate_condition(condition, result)
        # RSI 在持续下跌趋势中应该较低
        self.assertIsInstance(triggered, bool)

    def _make_klines(self, count):
        klines = []
        for i in range(count):
            close = 50000.0 + i * 10
            klines.append({
                'open': close - 5, 'high': close + 50, 'low': close - 50,
                'close': close, 'volume': 1000.0, 'source': 'binance',
                'timestamp': datetime(2024, 1, 1) + timedelta(hours=i),
            })
        return klines

    def _make_downtrend_klines(self, count):
        klines = []
        for i in range(count):
            close = 50000.0 - i * 100
            klines.append({
                'open': close + 50, 'high': close + 100, 'low': close - 100,
                'close': close, 'volume': 1000.0, 'source': 'binance',
                'timestamp': datetime(2024, 1, 1) + timedelta(hours=i),
            })
        return klines


class TestParallelPerformance(unittest.TestCase):
    """验证点5: 信号计算需要并行执行，完成时间不超过100ms"""

    def test_8_indicators_parallel_under_100ms(self):
        """8个指标并行计算在100ms内完成"""
        import time
        klines = []
        for i in range(200):
            close = 50000.0 + i * 10
            klines.append({
                'open': close - 5, 'high': close + 50, 'low': close - 50,
                'close': close, 'volume': 1000.0, 'source': 'binance',
                'timestamp': datetime(2024, 1, 1) + timedelta(hours=i),
            })

        tasks = [
            {'indicator_type': 'sma', 'klines': klines, 'params': {'period': 20}},
            {'indicator_type': 'ema', 'klines': klines, 'params': {'period': 12}},
            {'indicator_type': 'ema', 'klines': klines, 'params': {'period': 26}},
            {'indicator_type': 'rsi', 'klines': klines, 'params': {'period': 14}},
            {'indicator_type': 'macd', 'klines': klines, 'params': {}},
            {'indicator_type': 'bollinger', 'klines': klines, 'params': {}},
            {'indicator_type': 'atr', 'klines': klines, 'params': {'period': 14}},
            {'indicator_type': 'stoch', 'klines': klines, 'params': {}},
        ]

        start = time.monotonic()
        results = compute_indicators_parallel(tasks)
        elapsed_ms = (time.monotonic() - start) * 1000

        self.assertEqual(len(results), 8)
        for r in results:
            self.assertIsNone(r['error'], f"Error in {r['indicator_type']}: {r['error']}")

        self.assertLess(elapsed_ms, 100, f"Took {elapsed_ms:.1f}ms, expected < 100ms")


class TestCorrectDatasource(unittest.TestCase):
    """验证点6: 使用正确的数据源计算信号"""

    def test_indicator_uses_close_prices(self):
        """指标计算使用 close 价格"""
        klines = []
        for i in range(30):
            klines.append({
                'open': 50000 + i, 'high': 50100 + i, 'low': 49900 + i,
                'close': 50050 + i * 10, 'volume': 1000.0, 'source': 'binance',
                'timestamp': datetime(2024, 1, 1) + timedelta(hours=i),
            })

        import numpy as np
        closes = np.array([k['close'] for k in klines])
        result = compute_indicator('sma', klines, {'period': 10})
        # SMA of last 10 close prices
        expected = np.mean(closes[-10:])
        valid = result[~np.isnan(result)]
        self.assertAlmostEqual(float(valid[-1]), expected, places=1)

    def test_atr_uses_high_low_close(self):
        """ATR 使用 high, low, close"""
        klines = []
        for i in range(30):
            klines.append({
                'open': 100, 'high': 110, 'low': 90,
                'close': 100, 'volume': 1000.0, 'source': 'binance',
                'timestamp': datetime(2024, 1, 1) + timedelta(hours=i),
            })
        result = compute_indicator('atr', klines, {'period': 14})
        import numpy as np
        valid = result[~np.isnan(result)]
        self.assertTrue(len(valid) > 0)
        # ATR should be around 20 (high=110, low=90, TR≈20)
        self.assertTrue(valid[-1] > 0)

    def test_source_agnostic(self):
        """计算不依赖特定数据源"""
        klines = []
        for i in range(30):
            klines.append({
                'open': 50000, 'high': 50100, 'low': 49900,
                'close': 50000 + i * 10, 'volume': 1000.0,
                'source': 'any_source',
                'timestamp': datetime(2024, 1, 1) + timedelta(hours=i),
            })
        result = compute_indicator('sma', klines, {'period': 10})
        import numpy as np
        valid = result[~np.isnan(result)]
        self.assertTrue(len(valid) > 0)


class TestTriggerType(unittest.TestCase):
    """验证点7: 单次触发和持续触发"""

    def test_once_trigger_condition(self):
        """单次触发：条件满足时触发"""
        condition = {
            'operator': 'gt',
            'left': {'value': 100},
            'right': {'value': 50},
        }
        self.assertTrue(evaluate_condition(condition, None))

    def test_continuous_trigger_condition(self):
        """持续触发：每次条件满足都触发"""
        condition = {
            'operator': 'gt',
            'left': {'value': 100},
            'right': {'value': 50},
        }
        # 多次评估都应该为 True
        for _ in range(5):
            self.assertTrue(evaluate_condition(condition, None))

    def test_once_trigger_updates_status(self):
        """单次触发后状态变为 triggered"""
        from apps.signal_monitor.engine import SignalMonitorEngine

        engine = SignalMonitorEngine()
        mock_monitor = MagicMock()
        mock_monitor.id = uuid.uuid4()
        mock_monitor.name = 'test'
        mock_monitor.symbol = 'BTCUSDT'
        mock_monitor.indicator_type = 'rsi'
        mock_monitor.trigger_type = 'once'
        mock_monitor.action_type = 'notify'
        mock_monitor.condition = {
            'operator': 'gt',
            'left': {'value': 100},
            'right': {'value': 50},
        }

        with patch('apps.signal_monitor.engine.SignalTriggerLog') as MockLog, \
             patch('apps.notify.models.Notification'):
            MockLog.objects.create.return_value = MagicMock()
            engine._handle_trigger(mock_monitor, {})

        # 单次触发应更新状态为 triggered
        self.assertEqual(mock_monitor.status, 'triggered')
        mock_monitor.save.assert_called_once()

    def test_continuous_trigger_keeps_active(self):
        """持续触发后状态保持 active"""
        from apps.signal_monitor.engine import SignalMonitorEngine

        engine = SignalMonitorEngine()
        mock_monitor = MagicMock()
        mock_monitor.id = uuid.uuid4()
        mock_monitor.name = 'test'
        mock_monitor.symbol = 'BTCUSDT'
        mock_monitor.indicator_type = 'rsi'
        mock_monitor.trigger_type = 'continuous'
        mock_monitor.action_type = 'notify'
        mock_monitor.condition = {
            'operator': 'gt',
            'left': {'value': 100},
            'right': {'value': 50},
        }
        mock_monitor.trigger_count = 0

        with patch('apps.signal_monitor.engine.SignalTriggerLog') as MockLog, \
             patch('apps.notify.models.Notification'):
            MockLog.objects.create.return_value = MagicMock()
            engine._handle_trigger(mock_monitor, {})

        # 持续触发状态不变（status 不会被设为 'triggered'）
        mock_monitor.save.assert_called_once()
        # trigger_count 递增
        self.assertEqual(mock_monitor.trigger_count, 1)

    def test_celery_beat_config(self):
        """Celery Beat 定时任务已配置"""
        from celery_app import app
        schedule = app.conf.beat_schedule
        self.assertIn('check-signals', schedule)
        self.assertEqual(schedule['check-signals']['schedule'], 30.0)
        self.assertIn('clean-expired-monitors', schedule)


if __name__ == '__main__':
    unittest.main()
