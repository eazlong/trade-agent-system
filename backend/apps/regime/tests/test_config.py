"""统一配置面（第①段单元 2）。

这个文件测的不是「几个常量等于几」，而是**配置面作为一套机制是否成立**：

- 每个分组都必须是 frozen dataclass 且能 `dataclasses.replace` 派生——这是
  「留痕靠快照，不靠读回配置」的前置条件；
- 每个分组都必须能变成 JSON-safe 的字面量——判定记录要把它塞进 JSONField；
- 数值边界在 import 时就检查——坏编辑要在启动时炸，不是先安静地跑出一批怪判定；
- 阈值本身（14/20/60/250/0.80…）也被钉住：它们是「改阈值必须改代码发版」这句
  话的字面形态，无声漂移必须变成一次失败。
"""

from __future__ import annotations

import json
from dataclasses import (
    FrozenInstanceError,
    dataclass,
    fields,
    is_dataclass,
    replace,
)
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.regime import config


class TestGroupDiscipline(SimpleTestCase):
    """每个分组都满足「可派生 + 可快照 + 只读」三条。"""

    def test_every_group_is_a_frozen_dataclass_instance(self):
        for name, group in config.GROUPS.items():
            with self.subTest(group=name):
                self.assertTrue(
                    is_dataclass(group) and not isinstance(group, type),
                    f"{name} 应是 dataclass **实例**而非裸值或类——"
                    "裸值无法 replace 派生，类则会被 asdict 报错",
                )
                with self.assertRaises(FrozenInstanceError):
                    group.__setattr__("_probe", 1)

    def test_groups_hold_the_module_constants_themselves(self):
        """GROUPS 必须是常量的同一批对象：若它存的是副本，import 之后改常量
        （或反之）就会让「快照里的参数」和「判定真正用的参数」变成两套。"""
        self.assertIs(config.GROUPS["candles"], config.CANDLES)
        self.assertIs(config.GROUPS["judgement"], config.JUDGEMENT)
        self.assertIs(config.GROUPS["judgement_lifecycle"], config.JUDGEMENT_LIFECYCLE)
        self.assertIs(config.GROUPS["evidence"], config.EVIDENCE)

    def test_every_group_can_be_derived_with_replace(self):
        """测试靠 replace 临时改参数（不改全局），判定留痕也靠它。"""
        for name, group in config.GROUPS.items():
            with self.subTest(group=name):
                field_names = [f.name for f in fields(group)]
                self.assertTrue(field_names, f"{name} 没有任何字段")
                first = field_names[0]
                changed = replace(group, **{first: getattr(group, first)})
                self.assertEqual(changed, group)
                self.assertIsNot(changed, group)

    def test_replace_does_not_mutate_the_module_constant(self):
        derived = replace(config.JUDGEMENT, atr_period=99)
        self.assertEqual(derived.atr_period, 99)
        self.assertEqual(config.JUDGEMENT.atr_period, 14)


class TestSnapshot(SimpleTestCase):
    """快照是判定记录里那段「当时用的是哪套参数」的原文。"""

    def test_full_snapshot_is_json_serializable(self):
        """这是本机制的核心不变量：快照要能进 JSONField。"""
        encoded = json.dumps(config.full_snapshot(), ensure_ascii=False)
        self.assertIn("judgement", json.loads(encoded))

    def test_snapshot_keys_are_the_persistence_contract(self):
        """快照里的键会被写进历史记录，因此逐个钉死；改名必须伴随迁移。"""
        expected = {
            "candles": (
                "symbol",
                "exchange",
                "timeframe",
                "page_limit",
                "close_tolerance",
            ),
            "judgement": (
                "atr_period",
                "ema_fast_period",
                "ema_slow_period",
                "quantile_window_days",
                "high_vol_quantile",
                "ema_slope_lookback_days",
                "trend_separation_min_atr",
                "warmup_days",
            ),
            "judgement_lifecycle": ("min_dwell_days", "stale_after_days"),
            "evidence": ("min_trades", "min_months"),
        }
        snap = config.full_snapshot()
        self.assertEqual(set(snap), set(expected))
        for name, keys in expected.items():
            with self.subTest(group=name):
                self.assertEqual(set(snap[name]), set(keys))

    def test_timedelta_becomes_seconds(self):
        self.assertEqual(
            config.snapshot("candles")["candles"]["close_tolerance"], 300.0
        )

    def test_decimal_becomes_a_lossless_string(self):
        """金额口径恒用 Decimal；快照转 float 会丢精度，而它是事后归因的唯一依据。"""

        @dataclass(frozen=True)
        class WithDecimal:
            slippage_cap: Decimal = Decimal("0.005")

        with patch.dict(config.GROUPS, {"probe": WithDecimal()}):
            snap = config.snapshot("probe")

        self.assertEqual(snap["probe"]["slippage_cap"], "0.005")
        self.assertIsInstance(snap["probe"]["slippage_cap"], str)

    def test_nested_dict_and_list_values_are_converted(self):
        @dataclass(frozen=True)
        class WithNested:
            sources: dict = None
            windows: tuple = (timedelta(hours=96), Decimal("1.5"))

        group = WithNested(sources={"a": Decimal("2.0")})
        with patch.dict(config.GROUPS, {"probe": group}):
            snap = config.snapshot("probe")

        self.assertEqual(snap["probe"]["sources"], {"a": "2.0"})
        self.assertEqual(snap["probe"]["windows"], [345600.0, "1.5"])

    def test_unknown_group_name_raises(self):
        with self.assertRaises(KeyError) as ctx:
            config.snapshot("judgement", "jtidgement")
        self.assertIn("jtidgement", str(ctx.exception))

    def test_derived_warmup_is_in_the_snapshot_but_not_a_field(self):
        """warmup 刻意做成派生属性：两个独立字段会互相漂移，而漂移的表现是标注
        序列开头一段 NaN——看起来像「那几天没数据」，不像参数配错。"""
        judged = config.snapshot("judgement")["judgement"]
        self.assertEqual(judged["warmup_days"], 250)
        self.assertNotIn("warmup_days", [f.name for f in fields(config.JUDGEMENT)])

    def test_dumps_is_deterministic(self):
        self.assertEqual(config.dumps(), config.dumps())
        self.assertEqual(config.dumps("evidence"), config.dumps("evidence"))

    def test_two_snapshots_differ_when_a_threshold_differs(self):
        """比对两条记录是否同参，靠的就是这个。"""
        base = config.snapshot("judgement")
        patched = replace(config.JUDGEMENT, high_vol_quantile=0.9)
        with patch.dict(config.GROUPS, {"judgement": patched}):
            self.assertNotEqual(config.snapshot("judgement"), base)
        self.assertEqual(config.snapshot("judgement"), base)


class TestThresholdsAreTheDocumentedOnes(SimpleTestCase):
    """CONTEXT.md 里写下的默认值，字面钉死。

    「改阈值必须改代码发版」的下半句是「改了就一定看得见」：这条用例红了，说明
    有人动了参数，而参数会改变全系统行为。
    """

    def test_candle_defaults(self):
        self.assertEqual(config.CANDLES.symbol, "BTC/USDT")
        self.assertEqual(config.CANDLES.exchange, "binance")
        self.assertEqual(config.CANDLES.page_limit, 1000)
        self.assertEqual(config.CANDLES.close_tolerance, timedelta(minutes=5))

    def test_judgement_defaults(self):
        self.assertEqual(config.JUDGEMENT.atr_period, 14)
        self.assertEqual(config.JUDGEMENT.ema_fast_period, 20)
        self.assertEqual(config.JUDGEMENT.ema_slow_period, 60)
        self.assertEqual(config.JUDGEMENT.quantile_window_days, 250)
        self.assertEqual(config.JUDGEMENT.high_vol_quantile, 0.80)
        # 趋势判据的两个数（单元 3）：都是「有理由但未经验证」的取值，
        # 钉在这里是为了让将来在 Shadow 里调它们时留下一次可见的改动。
        self.assertEqual(config.JUDGEMENT.ema_slope_lookback_days, 5)
        self.assertEqual(config.JUDGEMENT.trend_separation_min_atr, 0.5)

    def test_lifecycle_defaults(self):
        self.assertEqual(config.JUDGEMENT_LIFECYCLE.min_dwell_days, 5)
        self.assertEqual(config.JUDGEMENT_LIFECYCLE.stale_after_days, 3)

    def test_evidence_defaults(self):
        self.assertEqual(config.EVIDENCE.min_trades, 30)
        self.assertEqual(config.EVIDENCE.min_months, 3)


class TestSelfValidation(SimpleTestCase):
    """坏编辑在 import 时炸，而不是先安静地跑出一批怪判定。"""

    def test_non_daily_timeframe_is_rejected(self):
        """判定与切片的日粒度口径（业务日 == UTC 开盘日 == 北京同日）建立在日线上。"""
        with self.assertRaises(ValueError) as ctx:
            config.CandleConfig(timeframe="4h")
        self.assertIn("1d", str(ctx.exception))

    def test_zero_page_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            config.CandleConfig(page_limit=0)

    def test_fast_ema_must_be_shorter_than_slow(self):
        with self.assertRaises(ValueError) as ctx:
            config.JudgementConfig(ema_fast_period=60, ema_slow_period=20)
        self.assertIn("ema_fast_period", str(ctx.exception))

    def test_equal_ema_periods_are_rejected(self):
        """等周期时间距恒为 0，趋势判据就永远不成立——那是参数错，不是市场平。"""
        with self.assertRaises(ValueError):
            config.JudgementConfig(ema_fast_period=20, ema_slow_period=20)

    def test_quantile_must_be_a_proper_fraction(self):
        for bad in (0.0, 1.0, -0.1, 1.5):
            with self.subTest(quantile=bad):
                with self.assertRaises(ValueError):
                    config.JudgementConfig(high_vol_quantile=bad)

    def test_atr_period_must_be_at_least_two(self):
        with self.assertRaises(ValueError):
            config.JudgementConfig(atr_period=1)

    def test_quantile_window_must_be_at_least_two(self):
        with self.assertRaises(ValueError):
            config.JudgementConfig(quantile_window_days=1)

    def test_slope_lookback_must_be_at_least_one_day(self):
        """回看 0 天 = 自己减自己，斜率恒为 0，趋势判据永远不成立。"""
        with self.assertRaises(ValueError):
            config.JudgementConfig(ema_slope_lookback_days=0)

    def test_trend_separation_threshold_must_be_positive(self):
        """门槛为 0 时任何一点噪声都算趋势；为负则符号判据被架空。"""
        for bad in (0, -0.5):
            with self.subTest(threshold=bad):
                with self.assertRaises(ValueError):
                    config.JudgementConfig(trend_separation_min_atr=bad)

    def test_negative_lifecycle_is_rejected(self):
        with self.assertRaises(ValueError):
            config.JudgementLifecycleConfig(min_dwell_days=-1)

    def test_evidence_floor_is_rejected(self):
        with self.assertRaises(ValueError):
            config.EvidenceConfig(min_trades=0)
        with self.assertRaises(ValueError):
            config.EvidenceConfig(min_months=0)

    def test_a_valid_derivation_passes_validation(self):
        """replace 派生也会过 __post_init__，好编辑不该被拦。"""
        tighter = replace(config.EVIDENCE, min_trades=40)
        self.assertEqual(tighter.min_trades, 40)


class TestWarmupCoupling(SimpleTestCase):
    def test_warmup_follows_the_quantile_window(self):
        """要算出第一个分位数就得先攒满一整个窗口，两者不可能独立取值。"""
        for window in (60, 250, 500):
            with self.subTest(window=window):
                cfg = config.JudgementConfig(quantile_window_days=window)
                self.assertEqual(cfg.warmup_days, window)
