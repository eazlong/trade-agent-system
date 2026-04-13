"""
技术指标计算测试

验证点：
- 各指标计算正确性
- 并行计算性能（<100ms��
"""

import time
import unittest
from datetime import datetime, timedelta

import numpy as np

from apps.signal_monitor.indicators import (
    compute_sma,
    compute_ema,
    compute_rsi,
    compute_macd,
    compute_bollinger,
    compute_atr,
    compute_stoch,
    compute_indicator,
    compute_indicators_parallel,
)


def _make_klines(
    count: int, base_price: float = 50000.0, volatility: float = 500.0
) -> list[dict]:
    """生成模拟 K 线数据"""
    np.random.seed(42)
    klines = []
    for i in range(count):
        change = np.random.randn() * volatility
        close = base_price + change
        high = close + abs(np.random.randn() * volatility * 0.3)
        low = close - abs(np.random.randn() * volatility * 0.3)
        open_price = base_price + np.random.randn() * volatility * 0.5
        klines.append(
            {
                "symbol": "BTCUSDT",
                "interval": "1h",
                "open_time": datetime(2024, 1, 1) + timedelta(hours=i),
                "close_time": datetime(2024, 1, 1) + timedelta(hours=i, minutes=59),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": abs(np.random.randn() * 1000),
                "turnover": abs(np.random.randn() * 50000000),
                "trades": int(abs(np.random.randn() * 10000)),
                "source": "binance",
                "timestamp": datetime(2024, 1, 1) + timedelta(hours=i),
            }
        )
    return klines


class TestSMA(unittest.TestCase):
    """SMA 计算测试"""

    def test_basic(self):
        closes = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = compute_sma(closes, 3)
        self.assertEqual(len(result), 5)
        self.assertTrue(np.isnan(result[0]))
        self.assertTrue(np.isnan(result[1]))
        self.assertAlmostEqual(result[2], 2.0)
        self.assertAlmostEqual(result[3], 3.0)
        self.assertAlmostEqual(result[4], 4.0)

    def test_period_equals_length(self):
        closes = np.array([10.0, 20.0, 30.0])
        result = compute_sma(closes, 3)
        self.assertAlmostEqual(result[2], 20.0)

    def test_insufficient_data(self):
        closes = np.array([1.0, 2.0])
        result = compute_sma(closes, 5)
        self.assertEqual(len(result), 0)


class TestEMA(unittest.TestCase):
    """EMA 计算测试"""

    def test_basic(self):
        closes = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
        result = compute_ema(closes, 3)
        self.assertEqual(len(result), 5)
        self.assertTrue(np.isnan(result[0]))
        self.assertTrue(np.isnan(result[1]))
        # EMA3 seed = mean([10, 11, 12]) = 11.0
        self.assertAlmostEqual(result[2], 11.0)
        # multiplier = 2/(3+1) = 0.5
        # EMA[3] = 13*0.5 + 11*0.5 = 12.0
        self.assertAlmostEqual(result[3], 12.0)

    def test_insufficient_data(self):
        closes = np.array([1.0])
        result = compute_ema(closes, 3)
        self.assertEqual(len(result), 0)


class TestRSI(unittest.TestCase):
    """RSI 计算测试"""

    def test_uptrend_rsi_high(self):
        """上升趋势 RSI 应该较高"""
        closes = np.array([float(i) for i in range(30)])
        result = compute_rsi(closes, 14)
        valid = result[~np.isnan(result)]
        self.assertTrue(len(valid) > 0)
        self.assertTrue(valid[-1] > 70, f"Uptrend RSI should be > 70, got {valid[-1]}")

    def test_downtrend_rsi_low(self):
        """下降趋势 RSI 应该较低"""
        closes = np.array([float(30 - i) for i in range(30)])
        result = compute_rsi(closes, 14)
        valid = result[~np.isnan(result)]
        self.assertTrue(len(valid) > 0)
        self.assertTrue(
            valid[-1] < 30, f"Downtrend RSI should be < 30, got {valid[-1]}"
        )

    def test_insufficient_data(self):
        closes = np.array([1.0, 2.0])
        result = compute_rsi(closes, 14)
        self.assertEqual(len(result), 0)


class TestMACD(unittest.TestCase):
    """MACD 计算测试"""

    def test_basic_structure(self):
        closes = np.array([float(i) for i in range(50)])
        result = compute_macd(closes)
        self.assertIn("macd", result)
        self.assertIn("signal", result)
        self.assertIn("histogram", result)
        self.assertEqual(len(result["macd"]), 50)

    def test_uptrend_macd_positive(self):
        """上升趋势 MACD 应为正"""
        closes = np.array([float(i**1.05) for i in range(1, 51)])
        result = compute_macd(closes)
        valid_macd = result["macd"][~np.isnan(result["macd"])]
        if len(valid_macd) > 0:
            self.assertTrue(valid_macd[-1] > 0)


class TestBollinger(unittest.TestCase):
    """布林带计算测试"""

    def test_basic_structure(self):
        closes = np.array([float(i) for i in range(30)])
        result = compute_bollinger(closes, 20, 2.0)
        self.assertIn("upper", result)
        self.assertIn("middle", result)
        self.assertIn("lower", result)
        self.assertEqual(len(result["upper"]), 30)

    def test_upper_above_lower(self):
        closes = np.array([float(i * 10 + np.random.randn() * 5) for i in range(30)])
        result = compute_bollinger(closes, 20, 2.0)
        valid_idx = ~np.isnan(result["upper"])
        upper = result["upper"][valid_idx]
        lower = result["lower"][valid_idx]
        if len(upper) > 0:
            self.assertTrue(
                np.all(upper >= lower),
                "Upper band should be >= lower band",
            )


class TestATR(unittest.TestCase):
    """ATR 计算测试"""

    def test_basic(self):
        highs = np.array(
            [
                105.0,
                108.0,
                103.0,
                110.0,
                107.0,
                112.0,
                109.0,
                115.0,
                108.0,
                113.0,
                106.0,
                111.0,
                108.0,
                114.0,
                109.0,
                116.0,
                110.0,
                113.0,
                107.0,
                112.0,
            ]
        )
        lows = np.array(
            [
                95.0,
                98.0,
                93.0,
                100.0,
                97.0,
                102.0,
                99.0,
                105.0,
                98.0,
                103.0,
                96.0,
                101.0,
                98.0,
                104.0,
                99.0,
                106.0,
                100.0,
                103.0,
                97.0,
                102.0,
            ]
        )
        closes = np.array(
            [
                100.0,
                103.0,
                98.0,
                105.0,
                102.0,
                107.0,
                104.0,
                110.0,
                103.0,
                108.0,
                101.0,
                106.0,
                103.0,
                109.0,
                104.0,
                111.0,
                105.0,
                108.0,
                102.0,
                107.0,
            ]
        )
        result = compute_atr(highs, lows, closes, 14)
        self.assertEqual(len(result), 20)
        valid = result[~np.isnan(result)]
        self.assertTrue(len(valid) > 0)
        self.assertTrue(np.all(valid > 0))


class TestStoch(unittest.TestCase):
    """随机指标计算测试"""

    def test_basic_structure(self):
        highs = np.random.uniform(100, 110, 30)
        lows = np.random.uniform(90, 100, 30)
        closes = np.random.uniform(95, 105, 30)
        result = compute_stoch(highs, lows, closes, 14, 3)
        self.assertIn("k", result)
        self.assertIn("d", result)
        self.assertEqual(len(result["k"]), 30)


class TestComputeIndicator(unittest.TestCase):
    """compute_indicator 统一接口测试"""

    def test_sma_via_interface(self):
        klines = _make_klines(50)
        result = compute_indicator("sma", klines, {"period": 20})
        self.assertEqual(len(result), 50)

    def test_unknown_indicator_raises(self):
        klines = _make_klines(10)
        with self.assertRaises(ValueError):
            compute_indicator("unknown_indicator", klines)


class TestParallelComputation(unittest.TestCase):
    """并行计算测试"""

    def test_parallel_basic(self):
        klines = _make_klines(100)
        tasks = [
            {"indicator_type": "sma", "klines": klines, "params": {"period": 20}},
            {"indicator_type": "ema", "klines": klines, "params": {"period": 20}},
            {"indicator_type": "rsi", "klines": klines, "params": {"period": 14}},
        ]
        results = compute_indicators_parallel(tasks)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertIsNone(r["error"])
            self.assertIsNotNone(r["result"])

    def test_parallel_performance_under_100ms(self):
        """验证并行计算完成时间不超过 100ms"""
        klines = _make_klines(200)
        tasks = [
            {"indicator_type": "sma", "klines": klines, "params": {"period": 20}},
            {"indicator_type": "ema", "klines": klines, "params": {"period": 12}},
            {"indicator_type": "ema", "klines": klines, "params": {"period": 26}},
            {"indicator_type": "rsi", "klines": klines, "params": {"period": 14}},
            {"indicator_type": "macd", "klines": klines, "params": {}},
            {"indicator_type": "bollinger", "klines": klines, "params": {"period": 20}},
            {"indicator_type": "atr", "klines": klines, "params": {"period": 14}},
            {"indicator_type": "stoch", "klines": klines, "params": {}},
        ]

        start = time.monotonic()
        results = compute_indicators_parallel(tasks)
        elapsed_ms = (time.monotonic() - start) * 1000

        self.assertEqual(len(results), 8)
        for r in results:
            self.assertIsNone(
                r["error"], f"Indicator {r['indicator_type']} failed: {r['error']}"
            )

        self.assertLess(
            elapsed_ms,
            100,
            f"Parallel computation took {elapsed_ms:.1f}ms, expected < 100ms",
        )

    def test_parallel_handles_errors(self):
        """错误指标不影响其他指标"""
        klines = _make_klines(50)
        tasks = [
            {"indicator_type": "sma", "klines": klines, "params": {"period": 20}},
            {"indicator_type": "unknown", "klines": klines, "params": {}},
            {"indicator_type": "rsi", "klines": klines, "params": {"period": 14}},
        ]
        results = compute_indicators_parallel(tasks)
        self.assertEqual(len(results), 3)
        self.assertIsNone(results[0]["error"])
        self.assertIsNotNone(results[1]["error"])
        self.assertIsNone(results[2]["error"])


if __name__ == "__main__":
    unittest.main()
