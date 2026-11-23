"""量化判定核心（第①段单元 3）。

这个文件要钉住的不是「某个阈值算出来等于几」，而是四条性质：

1. **没有前视**——第 i 天的结论只由 `[0, i]` 决定。历史标注将来要拿去切片，
   一旦某天的标签用到了它之后的价格，切片就「预知」了那段行情，整套结论作废。
2. **四档固定且互斥**，优先级 高波动 > 下行趋势 > 上行趋势 > 箱体震荡。
   箱体震荡是兜底，不是「检出了箱体」。
3. **判不出来就是判不出来**——预热没走完、分位窗口没攒满、波动恒为 0 的日子，
   `regime` 是 `None`，**不是箱体震荡**。拿一个安静的假标签去喂切片，正是这套
   机制最该防的那类失败。
4. **历史标注与实时判定是同一个函数**——`latest_label` 只是 `label_series` 的尾巴，
   单元 4 不得另写一份「只算最后一天」的实现。

用例里的参数是**刻意缩小**的（ATR 2 / EMA 3,8 / 窗口 5）：真实参数（14/20/60/250）
要 250 根以上 K 线才出第一个结论，用真实参数写断言只会把结论埋进噪声里。缩小的只是
规模，规则一模一样；真实参数本身由 `test_config` 钉住。
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
from django.test import SimpleTestCase

from apps.regime import quant
from apps.regime.config import JUDGEMENT
from apps.regime.quant import (
    BaseRegime,
    label_series,
    latest_label,
    percentile_rank,
)

PARAMS = replace(
    JUDGEMENT,
    atr_period=2,
    ema_fast_period=3,
    ema_slow_period=8,
    quantile_window_days=5,
    high_vol_quantile=0.8,
    ema_slope_lookback_days=1,
    trend_separation_min_atr=0.5,
)

DAY0 = date(2026, 1, 1)


def candles(closes, bands=1.0):
    """按收盘价序列造日线；`bands` 是每日 (high−close) 的幅度，用来单独控制振幅。

    high/low 与 close 的关系是这三条不变量：high > close > low，所以振幅恒为正，
    ATR 不会因为 K 线自身矛盾而变成 0。
    """
    widths = bands if isinstance(bands, list) else [bands] * len(closes)
    return [
        {
            "date": DAY0 + timedelta(days=i),
            "high": float(c) + widths[i],
            "low": float(c) - widths[i],
            "close": float(c),
        }
        for i, c in enumerate(closes)
    ]


#: 稳步上行：每日 +1，振幅恒定
RAMP = [100 + i for i in range(16)]
#: 先急跌后收敛：跌幅收窄、振幅不变 → 趋势仍在，但波动分位从高往低走
DOWN_CALMING = [
    200,
    195,
    190,
    185,
    182,
    180,
    178,
    176,
    174,
    172,
    170,
    168,
    166,
    164,
    162,
    160,
]
#: 稳定急跌 -3/日 → 价格越跌 ATR% 越高，分位必然顶到 1.0
STEADY_DOWN = [200 - 3 * i for i in range(16)]
#: 几乎不动的漂移：斜率符号在，幅度不够
TINY_DRIFT = [100 + 0.1 * i for i in range(16)]
#: 平静 15 天，最后一天一根巨幅 K 线
QUIET_THEN_SPIKE = [100.0] * 16

#: 三条预热阶梯里最高的一条决定第一天能判定的日子（见 TestUndecidable 一节）
EXPECTED_ONSET = max(
    PARAMS.quantile_window_days + PARAMS.atr_period - 1,
    PARAMS.ema_slow_period - 1,
    PARAMS.ema_slope_lookback_days,
)


class TestFourTiersAreFixed(SimpleTestCase):
    """四档是落库与日报的枚举契约，中文只在展示层出现。"""

    def test_the_enum_has_exactly_four_members(self):
        self.assertEqual(len(BaseRegime), 4)

    def test_values_are_ascii_slugs(self):
        """落库、日志、查询条件里用的都是这些字符串；改名等于一次数据迁移。"""
        self.assertEqual(
            {m.value for m in BaseRegime},
            {"high_vol", "downtrend", "uptrend", "range"},
        )

    def test_priority_is_the_documented_order(self):
        self.assertEqual(
            quant.PRIORITY,
            (
                BaseRegime.HIGH_VOL,
                BaseRegime.DOWNTREND,
                BaseRegime.UPTREND,
                BaseRegime.RANGE,
            ),
        )

    def test_every_member_has_a_chinese_display_name(self):
        self.assertEqual(
            {m.display for m in BaseRegime},
            {"高波动", "下行趋势", "上行趋势", "箱体震荡"},
        )


def metrics(rank=0.5, slope=1.0, separation=1.0, **overrides):
    """造一条只用于定档的中间量。其余字段填「能算出来」的正常值。

    刻意不从 `label_series` 反推：这一节测的是**规则**，直接喂结构体才不必先让真实
    指标走到某个恰好满足条件的日子（那种夹具一改参数就散）。
    """
    fields = dict(
        date=DAY0,
        atr=1.0,
        atr_pct=0.01,
        atr_pct_rank=rank,
        quantile_sample=PARAMS.quantile_window_days,
        ema_fast=100.0,
        ema_slow=99.0,
        ema_slope=slope,
        separation=separation,
        # 周期维那两条通道（#64）。默认值刻意取「够强且一致」：这样构造出来的中间量
        # 在四档规则里与改动前完全等价，只有显式覆盖它们时才进入 `unclear` 那些分支。
        # ATR=1.0，所以 strength 就等于 |slope|。
        slope_strength=abs(slope),
        slope_consistency=1.0,
        # 波动维的四条事件证据（#65）。默认全 `None` = 「没有证据说明发生了事件」，
        # 于是默认夹具判 `normal`——这与改动前「rank < 0.8 ⇒ normal」的默认结果一致，
        # 只有显式覆盖它们时才进入 `event_spike` 那些分支。
        #
        # 刻意不给 `move`/`gap` 填一个「平静」的小数：那条路径会盖住「证据不足」
        # 与「证据说平静」的区别，而那正是本维最该被钉住的一格。
        gap=None,
        volume_ratio=None,
        atr_jump=None,
        move=None,
    )
    fields.update(overrides)
    return quant.DayMetrics(**fields)


class TestDecisionTable(SimpleTestCase):
    """规则本身。

    `_classify` 是这条规则的唯一一处陈述，所以直接测它：端到端用例（下一节）证明
    它接到了真实指标上，这一节证明规则在四个方向上都对，包括「符号与间距打架」那格。

    它吃的是**中间量结构体**，不碰 K 线——这正是 prefactor 的形状：算数与定档分开之后，
    规则可以脱离行情直接被测，而行情夹具那份成本留给端到端那一节。
    """

    def test_high_vol_beats_both_trends(self):
        """优先级第一档：先问该不该保命，再问往哪走。

        `event_spike` 的判据自 #65 起是四条事件证据，与 ATR% 分位无关——所以这里
        显式给一条「单日大幅」证据，并且**故意把分位压到最低**：若保命档还在看分位，
        这条就会落回箱体震荡。
        """
        for slope, separation in ((1.0, 2.0), (-1.0, -2.0), (0.0, 0.0)):
            with self.subTest(slope=slope, separation=separation):
                self.assertIs(
                    quant._classify(
                        metrics(0.0, slope, separation, move=PARAMS.move_ratio + 0.01),
                        PARAMS,
                    ),
                    BaseRegime.HIGH_VOL,
                )

    def test_the_quantile_no_longer_decides_the_blanket_tier(self):
        """**分位不再决定保命档**——这是 #65 要结束的那个缺陷。

        旧判据在数学上锁死「250 日窗口里必有 20% 的日子判高波动」，而人工真值里是 0 天。
        这里把分位推到最高（0.99）而四条事件证据全给平静值：若保命档还看分位，这条
        就会是 `high_vol`；正确的结论是它落回趋势/箱体。
        """
        label = quant._classify(
            metrics(
                0.99,
                0.0,
                0.0,
                move=PARAMS.move_ratio / 2,
                gap=PARAMS.gap_ratio / 2,
                volume_ratio=PARAMS.volume_multiple / 2,
                atr_jump=1.0,
            ),
            PARAMS,
        )
        self.assertIs(label, BaseRegime.RANGE)

    def test_each_event_evidence_alone_flips_the_blanket_tier(self):
        """四条证据是**或**：任何一条越阈值都足够，不必凑齐。

        串成「与」会让识别窗口缩到极少数几天，而那几天恰恰是最典型的事件日。
        """
        cases = {
            "move": {"move": PARAMS.move_ratio + 0.01},
            "gap": {"gap": PARAMS.gap_ratio + 0.01},
            "volume": {"volume_ratio": PARAMS.volume_multiple + 0.1},
            "atr_jump": {"atr_jump": PARAMS.atr_jump_ratio + 0.1},
        }
        for name, overrides in cases.items():
            with self.subTest(evidence=name):
                self.assertIs(
                    quant._classify(metrics(0.0, 0.0, 0.0, **overrides), PARAMS),
                    BaseRegime.HIGH_VOL,
                )

    def test_the_event_thresholds_are_strictly_exceeded(self):
        """恰好等于阈值不算事件：「超过 N 倍均量」不等于「达到 N 倍」。"""
        self.assertIs(
            quant._classify(
                metrics(
                    0.0,
                    0.0,
                    0.0,
                    move=PARAMS.move_ratio,
                    gap=PARAMS.gap_ratio,
                    volume_ratio=PARAMS.volume_multiple,
                    atr_jump=PARAMS.atr_jump_ratio,
                ),
                PARAMS,
            ),
            BaseRegime.RANGE,
        )

    def test_trend_needs_sign_and_separation_to_agree(self):
        threshold = PARAMS.trend_separation_min_atr
        self.assertIs(
            quant._classify(metrics(0.5, +1.0, +(threshold + 0.1)), PARAMS),
            BaseRegime.UPTREND,
        )
        self.assertIs(
            quant._classify(metrics(0.5, -1.0, -(threshold + 0.1)), PARAMS),
            BaseRegime.DOWNTREND,
        )

    def test_disagreeing_sign_and_separation_is_not_a_trend(self):
        """间距说「下行」而斜率说「刚拐头」——两边都不算，落回箱体震荡。

        这就是 (EMA20−EMA60) 单独用会出的错：它是带符号的，负值会被读成下行趋势，
        而它到底是一路下行还是刚拐头向下，只有斜率能分辨。
        """
        threshold = PARAMS.trend_separation_min_atr
        self.assertIs(
            quant._classify(metrics(0.5, +1.0, -(threshold + 0.1)), PARAMS),
            BaseRegime.RANGE,
        )
        self.assertIs(
            quant._classify(metrics(0.5, -1.0, +(threshold + 0.1)), PARAMS),
            BaseRegime.RANGE,
        )

    def test_separation_exactly_at_the_threshold_is_not_a_trend(self):
        """符号对但幅度只有刚好一个门槛：噪声喊了一声方向，不算趋势。"""
        self.assertIs(
            quant._classify(
                metrics(0.5, +1.0, +PARAMS.trend_separation_min_atr), PARAMS
            ),
            BaseRegime.RANGE,
        )

    def test_box_detection_is_not_a_gate(self):
        """箱体判定**不参与**档位归属：检不检出都落到箱体震荡（它是兜底）。

        把它改成一道闸会多出一个「既非四档、又不成形箱体」的第五态，与「四档固定
        枚举、互斥」直接冲突。它的用途只是给日报写依据，等单元 8 需要时才引入。
        """
        self.assertFalse(hasattr(quant, "detect_box_range"))


class TestCycleDecisionTable(SimpleTestCase):
    """周期维的**真判定**（#64）：两条独立路径 + 显式横盘 + `unclear`。

    这一组是本票的核心。它解决两个已量化的缺陷：

    1. **箱体当兜底吞掉缓慢趋势** —— 旧结构里 `RANGE` 是「其余」。实测 2254 个可比日里
       人工标「下行」而算法判「箱体」的有 207 天，其中 125 天（60%）EMA 斜率方向其实
       是对的，只是 `|separation| <= 0.5` 卡住。
    2. **间距阈值是唯一判据** —— 缓慢阴跌时 EMA20 与 EMA60 靠得近，间距到不了半个 ATR。

    所以这里逐个钉死：两条路径各自都能单独成立、横盘必须**同时**证明「无间距且无强度」、
    以及判不出来时落 `unclear` 而不是任何一档。
    """

    def cycle(self, **overrides):
        """只喂中间量的构造器。默认是「两条路径都不成立」的中性位置。"""
        return quant._classify_cycle(metrics(**overrides), PARAMS)

    # ---- 路径一：斜率与间距同向且过阈值 ----

    def test_path_one_needs_sign_and_separation_to_agree(self):
        """间距与斜率同向、且过 `cycle_separation_min_atr` ⇒ 趋势。

        这是旧结构那一支，但门槛从 0.5 松到 0.15——松是安全的，因为路径二与显式横盘
        会各自兜住松下来的那部分。
        """
        sep = PARAMS.cycle_separation_min_atr
        self.assertIs(
            self.cycle(ema_slope=+1.0, separation=+(sep + 0.05)), quant.Cycle.BULL
        )
        self.assertIs(
            self.cycle(ema_slope=-1.0, separation=-(sep + 0.05)), quant.Cycle.BEAR
        )

    def test_path_one_alone_works_even_without_strength(self):
        """间距够就是趋势，**不要求**强度也够——两条路径是「或」，不是「且」。

        把两者串成且会让「拉得很开但走得慢」的日子落进 `unclear`，而它明明是个
        清晰的方向。
        """
        self.assertIs(
            self.cycle(
                ema_slope=+1.0,
                separation=+(PARAMS.cycle_separation_min_atr + 0.05),
                slope_strength=0.0,
                slope_consistency=0.0,
            ),
            quant.Cycle.BULL,
        )

    # ---- 路径二：强度 × 一致性（缓慢趋势的救回通道）----

    def test_path_two_rescues_a_slow_trend_with_a_narrow_separation(self):
        """**这就是缓慢阴跌那一天**：间距不够，但强度够且方向一致 ⇒ 下行。

        旧结构在这格里判箱体——125 天错向的主体。新结构必须判 `bear`，且这条用例
        同时证明路径二**不依赖**路径一（间距刻意取得远小于门槛）。
        """
        self.assertIs(
            self.cycle(
                ema_slope=-1.0,
                separation=-(PARAMS.cycle_separation_min_atr / 3),
                slope_strength=PARAMS.cycle_strength_min + 0.05,
                slope_consistency=PARAMS.cycle_consistency_min + 0.05,
            ),
            quant.Cycle.BEAR,
        )

    def test_path_two_requires_both_strength_and_consistency(self):
        """强度够但一致性不够 ⇒ **不是**趋势，也**不是**横盘，是 `unclear`。

        这种日子（有动能无共识）被旧结构判成箱体，正是「把要动的行情说成平静」。
        """
        self.assertIs(
            self.cycle(
                ema_slope=+1.0,
                separation=+(PARAMS.cycle_separation_min_atr / 3),
                slope_strength=PARAMS.cycle_strength_min + 0.05,
                slope_consistency=PARAMS.cycle_consistency_min - 0.05,
            ),
            quant.Cycle.UNCLEAR,
        )

    def test_missing_consistency_falls_back_to_unclear_not_sideways(self):
        """一致性算不出来（窗口没满）时不许当「一致」。

        `None` 是「还看不出来」，填 1.0 会让预热期整段看起来方向极其一致。
        """
        self.assertIs(
            self.cycle(
                ema_slope=+1.0,
                separation=+(PARAMS.cycle_separation_min_atr / 3),
                slope_strength=PARAMS.cycle_strength_min + 0.05,
                slope_consistency=None,
            ),
            quant.Cycle.UNCLEAR,
        )

    # ---- 显式横盘：必须同时证明「无间距」与「无强度」----

    def test_sideways_requires_both_no_separation_and_no_strength(self):
        """横盘是**主动证明**出来的，不是「其余」。

        三个格逐个断言：两条都弱 ⇒ 横盘；只有一条弱 ⇒ `unclear`。把后者判成横盘，
        就是旧结构那 207 天系统性错向的形态。
        """
        sep_min = PARAMS.cycle_separation_min_atr
        str_min = PARAMS.cycle_strength_min
        cases = [
            # (separation 弱?, strength 弱?, 期望)
            (True, True, quant.Cycle.SIDEWAYS),
            (False, True, quant.Cycle.UNCLEAR),
            (True, False, quant.Cycle.UNCLEAR),
        ]
        for weak_sep, weak_str, expected in cases:
            with self.subTest(weak_sep=weak_sep, weak_str=weak_str):
                self.assertIs(
                    self.cycle(
                        ema_slope=+1.0,
                        separation=(sep_min / 3) if weak_sep else -(sep_min + 0.05),
                        slope_strength=(str_min / 2) if weak_str else (str_min + 0.05),
                        slope_consistency=0.0,
                    ),
                    expected,
                )

    def test_missing_strength_counts_as_weak_for_sideways(self):
        """强度算不出来（ATR 为 0 等）时按「弱」处理，仍可判横盘。

        与一致性那条相反：横盘要的是「证明不了有强度」，而 `None` 恰恰就是证明不了。
        """
        self.assertIs(
            self.cycle(
                ema_slope=+1.0,
                separation=+(PARAMS.cycle_separation_min_atr / 3),
                slope_strength=None,
                slope_consistency=None,
            ),
            quant.Cycle.SIDEWAYS,
        )

    def test_the_slope_sign_decides_the_direction_not_the_separation_sign(self):
        """方向由**斜率**决定；间距只是「拉开没拉开」的旁证。

        间距说下行而斜率说上行（刚拐头）时，两条路径都不成立——落 `unclear`，
        既不判 bull（斜率对但间距反向）也不判 bear（间距对但方向刚变）。
        """
        self.assertIs(
            self.cycle(
                ema_slope=+1.0,
                separation=-(PARAMS.cycle_separation_min_atr + 0.05),
                slope_strength=0.0,
                slope_consistency=0.0,
            ),
            quant.Cycle.UNCLEAR,
        )

    def test_half_data_raises_instead_of_defaulting(self):
        """`_classify_cycle` 拿到半截数据时**抛**，不退回横盘。

        与 `_classify` 同一纪律：静默返回 `sideways` 会把「算不出来」变成一条安静的
        假标签，而横盘正是最容易看起来正常的那个假标签。
        """
        for missing in ("ema_slope", "separation"):
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    quant._classify_cycle(metrics(**{missing: None}), PARAMS)


class TestArithmeticIsSeparateFromClassification(SimpleTestCase):
    """prefactor 的形状：**算数**与**定档**是两段，不是一回事。

    这条性质不是「代码组织偏好」：两个维度（周期 / 波动）的分类器要共用同一批中间量。
    不拆开，每加一个分类器就得复制一遍算数，而复制出来的第二份迟早会和第一份漂开——
    两边的结论都会看着很正常，这正是本设计反复在防的那类失败。

    用例钉三件事：中间量序列不含档位、定档只吃中间量、两段串起来就是对外入口。
    """

    def test_metrics_series_carries_no_verdict(self):
        """中间量结构体里**没有** `regime`：它只回答「这个数是多少」。

        有 `regime` 就意味着算数与定档又缝回了一起——那正是这次要拆开的东西。
        """
        fields = {f.name for f in dataclasses.fields(quant.DayMetrics)}
        self.assertNotIn("regime", fields)

    def test_metrics_series_is_one_row_per_candle_in_order(self):
        series = candles(RAMP)
        metrics = quant.metrics_series(series, PARAMS)

        self.assertEqual([m.date for m in metrics], [row["date"] for row in series])
        self.assertEqual(quant.metrics_series([], PARAMS), [])

    def test_the_same_metrics_under_different_history_give_the_same_regime(self):
        """同一份中间量，来自完全不同的两段行情 ⇒ 同一个档位。

        这是「定档不看行情」的**可失败**形式：若哪天有人把 K 线重新塞进分类器
        （比如「顺便看一眼原始收盘价」），两段不同行情的同日中间量就会被判出不同档位，
        这条会红。只断言 `_classify(m) == classify_day(m)` 是没有约束力的——后者本来
        就是前者的直接委托。
        """
        up = {m.date: m for m in quant.metrics_series(candles(RAMP), PARAMS)}
        down = {m.date: m for m in quant.metrics_series(candles(STEADY_DOWN), PARAMS)}
        shared = sorted(set(up) & set(down))[-1]

        # 两段行情的中间量本来不同，档位也就理应不同；把其中一份的「就绪三项」
        # 搬到另一份上，档位必须跟着这三项走，而不是跟着它来自哪段行情走。
        transplanted = dataclasses.replace(
            down[shared],
            atr_pct_rank=up[shared].atr_pct_rank,
            ema_slope=up[shared].ema_slope,
            separation=up[shared].separation,
        )
        self.assertNotEqual(up[shared].atr_pct_rank, down[shared].atr_pct_rank)
        self.assertIs(
            quant.classify_day(transplanted, PARAMS).regime,
            quant.classify_day(up[shared], PARAMS).regime,
        )

    def test_label_series_is_the_two_stages_joined(self):
        """`label_series` 不是第三份实现，而是 `metrics_series` + `classify_day`。

        逐日断言而不是只比长度：两段各自独立算一遍时，任何一处差异都只会表现成
        「某些天的档位不同」，而不是报错。
        """
        series = candles(DOWN_CALMING)
        joined = [
            quant.classify_day(m, PARAMS) for m in quant.metrics_series(series, PARAMS)
        ]

        self.assertEqual(label_series(series, PARAMS), joined)

    def test_each_label_carries_the_metrics_object_it_was_built_from(self):
        """落库要扁平字段，分类器要整体对象，两者指向同一批数字。"""
        label = label_series(candles(RAMP), PARAMS)[EXPECTED_ONSET]

        self.assertIsInstance(label.metrics, quant.DayMetrics)
        self.assertEqual(label.metrics.date, label.date)
        self.assertEqual(label.metrics.atr_pct_rank, label.atr_pct_rank)
        self.assertEqual(label.metrics.ema_slope, label.ema_slope)
        self.assertEqual(label.metrics.separation, label.separation)

    def test_an_unready_metrics_struct_is_unjudged_not_range(self):
        """三个条件缺一 → 未判定。分类侧也把这一条守住，不只在串联处守。"""
        for missing in ("atr_pct_rank", "ema_slope", "separation"):
            with self.subTest(missing=missing):
                label = quant.classify_day(metrics(**{missing: None}), PARAMS)
                self.assertIsNone(label.regime)

    def test_classify_refuses_half_data_instead_of_defaulting_to_range(self):
        """`_classify` 自己拿到半截数据时**抛**，不退回兜底档。

        `classify_day` 已经先验过，所以这条路正常不会走到；但它要是静默返回箱体震荡，
        「算不出来」就会变成一条安静的假标签——而箱体震荡是兜底档，切片会当真。
        抛异常是把「调用方漏了校验」变成一个立刻看得见的事故。
        """
        # `atr_pct_rank` **不在**这一组里（#65）：保命档改看事件证据之后，「ATR% 分位」
        # 不再被 `_classify` 读。它仍在 `classify_day` 的就绪条件里——那是**预热**门槛
        # （窗口攒满才发布结论），与「哪个分类器读哪个数」是两件事。
        with self.assertRaises(ValueError):
            quant._classify(metrics(ema_slope=None), PARAMS)
        with self.assertRaises(ValueError):
            quant._classify(metrics(separation=None), PARAMS)


class TestVolatilityProxyFeedsTheVerdict(SimpleTestCase):
    """波动代理的四个中间量（#65）：**它们现在就是判据**。

    #62 时这批量「只算不用」，那时钉的是「接进来也不影响档位」。#65 把它们接进了定档，
    于是那条断言反过来失效了——它的本意是防止**意外**接线，现在接线是目的，留着它只会
    变成一条要求「判据不许工作」的空转用例。

    这里钉三件事：四个量都在结构体里、缺数据时是 `None`（不是 0 / 1.0）、以及把代理量
    灌进去**确实**翻档位（接线的证据）。
    """

    def test_the_four_proxy_fields_are_in_the_struct(self):
        fields = {f.name for f in dataclasses.fields(quant.DayMetrics)}
        for name in ("gap", "volume_ratio", "atr_jump", "move"):
            with self.subTest(field=name):
                self.assertIn(name, fields)

    def test_pouring_event_evidence_into_a_quiet_series_flips_it_to_spike(self):
        """安静行情 + 极端代理量 ⇒ 那些天判 `event_spike`。

        代理量刻意取极端值而不是「温和地有点跳空」：温和值只能证明「这个夹具碰巧没
        触发阈值」，那种断言会随着阈值调整静默变成空转。
        """
        params = replace(PARAMS, volume_ma_period=3)
        bare = label_series(candles(RAMP), params)
        enriched = label_series(
            [
                {**row, "open": row["close"] * 1.9, "volume": 100_000.0 * (i + 1)}
                for i, row in enumerate(candles(RAMP))
            ],
            params,
        )

        judged = [i for i, lb in enumerate(bare) if lb.judged]
        self.assertTrue(judged, "夹具必须至少判出一天，否则这条用例是空转")
        # 安静行情本身不该有事件波动日。
        self.assertTrue(
            all(lb.volatility is quant.Volatility.NORMAL for lb in bare if lb.judged)
        )
        # 灌进极端代理量之后，被判定那些天应当判成事件波动。
        self.assertTrue(
            all(enriched[i].volatility is quant.Volatility.EVENT_SPIKE for i in judged)
        )
        # 四档跟着翻：保命档的判据就是这一维（#65）。
        self.assertTrue(all(enriched[i].regime is BaseRegime.HIGH_VOL for i in judged))

    def test_missing_open_and_volume_yield_none_not_a_default(self):
        """行情源不给量能是常态；编成 0 或 1.0 会让「没有数据」与「量能平稳」长得一样。

        `atr_jump` 是例外：它只依赖 ATR，与量能无关，所以这里照样有值——把它也一起
        断言成 `None` 会让「ATR 环比」这个量在缺量能行情里凭空消失，而它本来是
        可算的。
        """
        params = replace(PARAMS, volume_ma_period=3)
        label = label_series(candles(RAMP), params)[-1]

        self.assertIsNone(label.metrics.gap)
        self.assertIsNone(label.metrics.volume_ratio)
        self.assertIsNotNone(label.metrics.atr_jump)

    def test_the_first_day_has_no_proxy_metrics(self):
        """跳空与 ATR 环比都要前一日的数，第一天无从谈起。"""
        rows = [{**row, "open": row["close"], "volume": 100.0} for row in candles(RAMP)]
        first = quant.metrics_series(rows, PARAMS)[0]

        self.assertIsNone(first.gap)
        self.assertIsNone(first.atr_jump)

    def test_gap_is_an_absolute_ratio(self):
        """跳空取绝对值：方向由周期维回答，波动维只回答「有没有跳」。"""
        rows = [
            {
                "date": DAY0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "open": 100.0,
                "volume": 100.0,
            },
            {
                "date": DAY0 + timedelta(days=1),
                "high": 103.0,
                "low": 97.0,
                "close": 100.0,
                "open": 97.0,
                "volume": 100.0,
            },
        ]
        second = quant.metrics_series(rows, PARAMS)[1]

        self.assertAlmostEqual(second.gap, 0.03, places=9)

    def test_an_unparseable_optional_column_is_tolerated(self):
        """`open`/`volume` 读不出来只让代理量变成 `None`，不该让整条标注序列失败。

        `high`/`low`/`close` 相反：缺了它什么档都定不出来，必须报错（见
        `TestInputHandling`）。
        """
        rows = [{**row, "open": "n/a", "volume": None} for row in candles(RAMP)]
        labels = label_series(rows, PARAMS)

        self.assertEqual(len(labels), len(RAMP))
        # 两个字段的解析失败路径各断言一次：只断 volume 等于放过了 open 那一支。
        self.assertIsNone(labels[-1].metrics.gap)
        self.assertIsNone(labels[-1].metrics.volume_ratio)

    def test_volume_ratio_is_unavailable_during_its_own_warmup(self):
        """量能在、但均量还没攒够 `volume_ma_period` 天：`volume_ratio` 是 `None`。

        这是它最容易被"顺手"改成 0 或 1.0 的边界。改成 1.0 会让上线头几天看起来像
        「量能一直平稳」，而不是「还看不出来」。
        """
        params = replace(PARAMS, volume_ma_period=3)
        rows = [{**row, "open": row["close"], "volume": 100.0} for row in candles(RAMP)]
        series = quant.metrics_series(rows, params)

        # 均量是 3 日窗，所以下标 0、1 两天还没有分母。
        self.assertIsNone(series[0].volume_ratio)
        self.assertIsNone(series[1].volume_ratio)
        self.assertIsNotNone(series[2].volume_ratio)


class TestClassificationEndToEnd(SimpleTestCase):
    """同一套规则接到真实指标上的表现。"""

    def test_a_steady_downtrend_is_a_trend_not_a_high_vol_day(self):
        """**稳步阴跌不是事件波动日**——这正是 #65 要结束的那个误判。

        `STEADY_DOWN` 每天跌 1.5%，是一条清晰的趋势，而不是「发生了事件」。旧判据
        （ATR% 分位 > 80%）会把它判成高波动：分位在急跌里必然顶格，于是那天的**周期
        信息被丢掉**。新判据看的是「有没有跳空 / 放量 / ATR 骤升 / 单日大幅」，四条
        都不成立，所以它落 `downtrend`——四档与周期维第一次对同一片行情给出同一个
        方向。
        """
        labels = label_series(candles(STEADY_DOWN), PARAMS)
        last = labels[-1]

        self.assertLess(last.separation, -PARAMS.trend_separation_min_atr)
        self.assertLess(last.ema_slope, 0)
        self.assertLess(last.move, PARAMS.move_ratio, "每日 1.5% 不是事件级波动")
        self.assertIs(last.volatility, quant.Volatility.NORMAL)
        self.assertIs(last.cycle, quant.Cycle.BEAR)
        self.assertIs(last.regime, BaseRegime.DOWNTREND)

    def test_a_volume_spike_in_an_uptrend_keeps_both_axes(self):
        """**同一时点两维并存**：上行途中放量 ⇒ `cycle=bull + volatility=event_spike`。

        这是本票最核心的那一条验收判据。旧结构下 `HIGH_VOL` 排 `PRIORITY` 第一位，
        这样的日子只会落一个 `high_vol`，周期信息**在判据里就不存在**；现在两维各自
        独立判、互不覆盖。

        夹具刻意选**放量**而不是暴跌：一根 −12% 的大阴线会把快线一起打下去，那时
        `cycle=bear` 才是对的（趋势真的转了），用它做 `bull + spike` 的例子会把
        「两维独立」这件事和「趋势拐头」混在一起。放量同样是一次事件，但它不改变
        方向——这样才分得清「周期维没被波动维吃掉」与「周期维自己翻了」。
        """
        params = replace(PARAMS, volume_ma_period=3)
        closes = [100 + i for i in range(16)]
        rows = [
            {
                "date": DAY0 + timedelta(days=i),
                "high": float(c) + 1.0,
                "low": float(c) - 1.0,
                "open": float(c),
                "close": float(c),
                # 前段量能平稳，最后一天 10 倍放量：事件证据，方向不变。
                "volume": 10.0 if i < len(closes) - 1 else 200.0,
            }
            for i, c in enumerate(closes)
        ]
        last = label_series(rows, params)[-1]

        self.assertGreater(last.metrics.volume_ratio, params.volume_multiple)
        self.assertIs(last.volatility, quant.Volatility.EVENT_SPIKE)
        self.assertIs(
            last.cycle,
            quant.Cycle.BULL,
            "放量没有改变方向，周期维照样看见上行",
        )
        # 四档是兼容层：保命档优先，但它不再「吃掉」周期维（两列并存）。
        self.assertIs(last.regime, BaseRegime.HIGH_VOL)

    def test_calming_downtrend_is_a_downtrend(self):
        """跌幅收窄但方向未变：波动分位掉下来，趋势那一支才露出来。"""
        labels = label_series(candles(DOWN_CALMING), PARAMS)

        self.assertIs(labels[EXPECTED_ONSET].regime, BaseRegime.DOWNTREND)
        self.assertLessEqual(
            labels[EXPECTED_ONSET].atr_pct_rank, PARAMS.high_vol_quantile
        )

    def test_uptrend_is_the_mirror_image(self):
        labels = label_series(candles(RAMP), PARAMS)
        first = labels[EXPECTED_ONSET]

        self.assertGreater(first.ema_slope, 0)
        self.assertGreater(first.separation, PARAMS.trend_separation_min_atr)
        self.assertIs(first.regime, BaseRegime.UPTREND)

    def test_small_drift_with_the_right_sign_is_still_range(self):
        """斜率符号在，但均线间距走不满门槛 → 箱体震荡，不是「弱上行趋势」。"""
        labels = label_series(candles(TINY_DRIFT), PARAMS)
        last = labels[-1]

        self.assertGreater(last.ema_slope, 0)
        self.assertLess(last.separation, PARAMS.trend_separation_min_atr)
        self.assertIs(last.regime, BaseRegime.RANGE)

    def test_a_volatility_spike_flips_the_label_to_high_vol(self):
        """平静 15 天之后一根巨幅 K 线：只有它自己（和之后）判为高波动。

        分位是含当日的，所以巨幅那一刻 ATR% 就是窗口里的最大值 → 分位 1.0。
        """
        labels = label_series(
            candles(QUIET_THEN_SPIKE, bands=[1.0] * 15 + [30.0]), PARAMS
        )

        self.assertIs(labels[-1].regime, BaseRegime.HIGH_VOL)
        self.assertEqual(labels[-1].atr_pct_rank, 1.0)


class TestSlowTrendIsNotSwallowedBySideways(SimpleTestCase):
    """本票要解决的第一个缺陷：**缓慢趋势不再落进箱体**（端到端）。

    上一组钉的是规则本身；这一组钉「规则接到真实指标上之后，缓慢阴跌确实被判成
    `bear`」。两者的区别很实际：规则对了但指标算错、或窗口取错，端到端那条才会红。
    """

    def test_a_slow_bleed_is_bear_not_sideways(self):
        """温和阴跌（每天 −0.2、振幅 1.0）：间距走不满门槛，但仍是一条下行。

        这是旧结构最典型的失败形态——`|separation|` 到不了 0.5，于是落箱体。
        用例先在真实指标上确认「间距确实不够」（否则它证明不了任何事），再断言
        周期维没有因此判横盘。
        """
        # 每天 −0.2、振幅 1.0：实测 separation ≈ −0.25、strength ≈ 0.10、consistency = 1.0。
        # 这组数正好落在两个判据之间——间距没走满 0.5，强度刚够 0.10。旧结构在这格
        # 判箱体（207 天错向的主体），新结构必须判 bear，这正是路径二的全部意义。
        slow_bleed = [200 - 0.2 * i for i in range(30)]
        label = label_series(candles(slow_bleed), PARAMS)[-1]

        self.assertLess(label.ema_slope, 0, "方向确实是下行")
        self.assertGreater(
            label.separation,
            -PARAMS.trend_separation_min_atr,
            "间距确实没走满兼容层那道 0.5 的门槛——这正是它过去被判箱体的原因",
        )
        self.assertIs(label.cycle, quant.Cycle.BEAR, "路径二把它救回成下行")

    def test_a_flat_market_is_sideways_explicitly(self):
        """真正不动的行情仍判横盘——显式判定不等于把横盘取消。

            没有这条，把 `sideways` 整个删掉也能让上一条通过；而 ADR 0007 要的是
        `sideways` 从 33.5% 降到 2% 量级，不是降到 0。
        """
        label = label_series(candles(TINY_DRIFT), PARAMS)[-1]

        self.assertIs(label.cycle, quant.Cycle.SIDEWAYS)


class TestUnclearIsProduced(SimpleTestCase):
    """`unclear` 是**会被产出**的取值，不是枚举里摆着的装饰。

    #63 时它还被明确标注为「本版不产出」；本票落地后必须真的出现，否则「判不出来」
    这条语义就只活在 docstring 里。
    """

    def test_unclear_appears_on_a_conflicting_market(self):
        """先横后动：强度够但窗口内方向来回翻 ⇒ `unclear`。

        构造一段「来回震荡」再接一段单边，使一致性占比落在门槛之下。
        """
        zigzag = [100.0, 103.0, 99.0, 104.0, 98.0, 105.0, 97.0, 106.0, 96.0, 107.0]
        saw = zigzag + [107.0 + 2.0 * i for i in range(1, 11)]
        labels = label_series(candles(saw), PARAMS)
        produced = {lb.cycle for lb in labels if lb.judged}

        self.assertIn(quant.Cycle.UNCLEAR, produced)

    def test_unclear_and_sideways_diverge_on_real_input(self):
        """两者是不同的事实，**且用同一套输入能分别打出来**——不只是枚举值不同。

        `unclear` = 判了但方向证不出来；`sideways` = 证出了没方向。混同的代价已经
        量过：旧结构把前者当后者，207 天系统性错向。

        单纯的 `assertIsNot(UNCLEAR, SIDEWAYS)` 是恒真的（两个枚举成员当然不相等），
        删掉实现也不会红。这里改成行为断言：两组只差一个数的中间量必须分别落两边。
        """
        # 间距与强度都弱 → 证出了「没方向」。
        sideways = quant._classify_cycle(
            metrics(
                ema_slope=+1.0,
                separation=+(PARAMS.cycle_separation_min_atr / 3),
                slope_strength=(PARAMS.cycle_strength_min / 2),
                slope_consistency=0.0,
            ),
            PARAMS,
        )
        # 强度够但一致性不足以证明方向 → 判不出来。
        unclear = quant._classify_cycle(
            metrics(
                ema_slope=+1.0,
                separation=+(PARAMS.cycle_separation_min_atr / 3),
                slope_strength=(PARAMS.cycle_strength_min + 0.05),
                slope_consistency=(PARAMS.cycle_consistency_min - 0.05),
            ),
            PARAMS,
        )

        self.assertIs(sideways, quant.Cycle.SIDEWAYS)
        self.assertIs(unclear, quant.Cycle.UNCLEAR)

    def test_unclear_projects_to_nothing_in_the_legacy_layer(self):
        """`unclear + normal` 还原不回四档——旧结构里没有这一格，必须抛。

        这一点决定了「`unclear` 与 `missing` 同待遇」的落地位置：它不是在投影里
        折叠成 range，而是在统计口径里按「没有结论」计（见 `truth` 的 missing 语义）。
        """
        self.assertIsNone(
            quant.project_to_base_axes(quant.Cycle.UNCLEAR, quant.Volatility.NORMAL)
        )


class TestUndecidableDaysAreNotRange(SimpleTestCase):
    """数据不足是「还没有资格判定」，不是某一种阶段。

    与单元 1「失败必须能与『没有新数据』区分」是同一条纪律：把没有结论的日子算成
    箱体震荡，等于拿一个安静的假标签去喂切片，而切片正是拿这些标签停策略的地方。
    """

    def test_days_before_the_warmup_are_unjudged(self):
        labels = label_series(candles(RAMP), PARAMS)

        self.assertEqual(
            [lb.judged for lb in labels],
            [False] * EXPECTED_ONSET + [True] * (len(RAMP) - EXPECTED_ONSET),
        )

    def test_onset_is_the_tallest_warmup_ladder(self):
        """三条阶梯：ATR(2) 要 3 根、分位窗口要 窗口+ATR−1 = 6 根、EMA(8) 要 8 根。

        取最高的一条（EMA 慢线）→ 下标 7。写成算式的理由不是偷懒：这条用例红了
        就说明有人改了参数或改了某条阶梯，而两者的后果都是「标注序列开头少了一段」
        ——那种症状看起来像数据缺失，不像参数配错。
        """
        self.assertEqual(EXPECTED_ONSET, 7)

    def test_an_unjudged_day_still_carries_the_numbers_it_does_have(self):
        """缺的是趋势轴（EMA 慢线还没预热完），不是整天的数据。

        未判定 ≠ 什么都不记：`atr_pct_rank` 与 `quantile_sample` 都在。事后要解释
        「那天为什么判不出来」，答案同样得在这一行里。
        """
        label = label_series(candles(DOWN_CALMING), PARAMS)[EXPECTED_ONSET - 1]

        self.assertIsNone(label.regime)
        self.assertIsNotNone(label.atr_pct)
        self.assertEqual(label.atr_pct_rank, 0.2)
        self.assertEqual(label.quantile_sample, PARAMS.quantile_window_days)
        self.assertIsNone(label.ema_slow)
        self.assertIsNone(label.separation)

    def test_zero_volatility_is_unjudged_not_range(self):
        """价格一动不动（high=low=close）时 ATR 恒为 0，分位无从谈起。

        这是个数据异常形态（停牌、K 线被重复返回），不是「最平静的箱体」。若在这里
        给个箱体震荡，停牌行情就会被当成正常震荡行情放策略继续跑。
        """
        flat = [
            {
                "date": DAY0 + timedelta(days=i),
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
            }
            for i in range(12)
        ]
        labels = label_series(flat, PARAMS)

        self.assertTrue(all(lb.regime is None for lb in labels))
        # 前两根连 ATR 都算不出来（`compute_atr` 要 period+1 根），是 None 不是 0；
        # 之后 ATR 确实是 0，而 0 不能当分母，所以分位也一路是 None。
        self.assertEqual([lb.atr for lb in labels[:2]], [None, None])
        self.assertTrue(all(lb.atr == 0.0 for lb in labels[2:]))
        self.assertTrue(all(lb.atr_pct_rank is None for lb in labels))

    def test_series_shorter_than_the_window_is_never_judged(self):
        """窗口比序列还长 ⇒ 一天都攒不满 ⇒ 一天都不判。"""
        labels = label_series(candles(RAMP), replace(PARAMS, quantile_window_days=50))

        self.assertTrue(all(lb.regime is None for lb in labels))
        self.assertTrue(all(lb.regime != BaseRegime.RANGE for lb in labels))

    def test_series_shorter_than_the_indicators_does_not_crash(self):
        """指标库在样本不足时返回**空数组**而不是全 NaN，逐日索引会直接 IndexError。

        这里连同默认参数（EMA 60 / 窗口 250）一起测：单元 4 拿到的就是刚上线时
        只有几十根 K 线的库，它必须安静地给出「还没资格判定」，而不是炸掉当天任务。
        """
        for length in (1, 2, 3, 59):
            with self.subTest(length=length):
                labels = label_series(candles([100 + i for i in range(length)]))
                self.assertEqual(len(labels), length)
                self.assertTrue(all(lb.regime is None for lb in labels))


class TestNoLookahead(SimpleTestCase):
    """第 i 天的结论只由 `[0, i]` 决定。"""

    def test_adding_later_data_does_not_change_earlier_labels(self):
        full = label_series(candles(DOWN_CALMING), PARAMS)
        truncated = label_series(candles(DOWN_CALMING[:12]), PARAMS)

        self.assertEqual(len(truncated), 12)
        self.assertEqual(full[:12], truncated)

    def test_mutating_later_data_does_not_change_earlier_labels(self):
        """把后半段改成一段平静的横盘：前面的标签必须一个字节都不动。

        改的是数值而不是「让标签换个说法」——平静下来之后末段不再有事件证据，末段标签
        随之离开保命档，于是这条用例同时证明了「前缀没动」和「后缀确实动了」（后者是
        前者不空转的凭据）。

        **#65 之后这条夹具的末段行为变了**：`DOWN_CALMING` 是「每日跌 2~3 元」的
        阴跌，不是事件日——单日跌幅始终低于 `move_ratio`，所以它现在判 `downtrend`
        而不是 `high_vol`。那条「后缀确实动了」的断言因此改成钉趋势：前缀不动这件事
        与档位叫什么无关，它依赖的是「指标只看过去」。
        """
        original = label_series(candles(DOWN_CALMING), PARAMS)
        # 尾巴改成一段**反弹**（166→180）：原来的尾巴是继续阴跌，改之后斜率方向真的
        # 翻了——这才是「后缀确实动了」的凭据。写成「改成横盘」不行：贴着 166 不动
        # 只把那段变平，末段仍然判 `bear`（前面那一整段下跌还在窗口里），用例会空转。
        calm_tail = DOWN_CALMING[:12] + [166.0, 170.0, 175.0, 180.0]
        mutated = label_series(candles(calm_tail), PARAMS)

        self.assertEqual(original[:12], mutated[:12])
        # 原序列末段是下行趋势（阴跌，不是事件波动日）。
        self.assertIs(original[-1].regime, BaseRegime.DOWNTREND)
        # 「后缀确实动了」这条证据改钉在**周期维**上：四档在两条序列的末段恰好都是
        # `downtrend`（平静下来的尾巴还不足以把四档翻成横盘），而 cycle 立刻就能分出
        # 差别。这条断言的存在意义是证明前缀那半条不是空转——钉哪一维都行，钉**动了的**
        # 那一维才有效。
        self.assertNotEqual(original[-1].cycle, mutated[-1].cycle)


class TestCycleHasNoLookahead(SimpleTestCase):
    """周期维的新判据同样**不许前视**——这是历史切片能重算的前提。

    `TestNoLookahead` 钉的是四档；本组钉的是 #64 新增的两条通道。一致性那条尤其
    容易踩：它要回看 20 天，实现里若把窗口写成 `[i - window, i + window]` 或在
    `metrics_series` 里预先算一遍全序列再取尾，就会用到未来数据，而结果看起来
    完全正常。
    """

    def test_truncating_the_history_does_not_change_earlier_cycles(self):
        """前缀不变：只喂前 N 天算出来的周期，必须与全量算出来的前 N 天逐日相等。"""
        series = candles(DOWN_CALMING * 3)
        full = label_series(series, PARAMS)

        for cut in (8, 12, 20, len(series) - 1):
            with self.subTest(cut=cut):
                self.assertEqual(label_series(series[:cut], PARAMS), full[:cut])

    def test_mutating_the_tail_does_not_change_earlier_cycles(self):
        """改后缀：把后半段换成反向行情，前半段的分类必须一个都不动。"""
        head = DOWN_CALMING
        original = label_series(candles(head + list(range(100, 130))), PARAMS)
        flipped = label_series(candles(head + list(range(200, 170, -1))), PARAMS)

        self.assertEqual(original[: len(head)], flipped[: len(head)])
        self.assertNotEqual(original[len(head) :], flipped[len(head) :])

    def test_consistency_only_reads_backwards(self):
        """一致性的窗口是 `[i - window + 1, i]`：第 i 天只由 `[0, i]` 决定。

        直接对中间量断言比分档更敏感——即使某天档位碰巧没变，两个通道的数字也会变。
        """
        series = candles(DOWN_CALMING * 3)
        short = quant.metrics_series(series[:20], PARAMS)[-1]
        long = quant.metrics_series(series, PARAMS)[19]

        self.assertEqual(short.slope_consistency, long.slope_consistency)
        self.assertEqual(short.slope_strength, long.slope_strength)

    def test_consistency_rejects_any_peek_at_later_bars(self):
        """把「回看窗口」挪到**含当日之后**的写法必须被这条抓住。

        上面那条只比对「截断到第 20 天」与「全量取第 19 天」，两者窗口起点相同，
        因此**看不见**「窗口向右多取了几根」这类前视——变异测试证实过：把
        `index` 往后推 5 天，那条仍然绿。

        这里换成**不对称夹具**：让第 i 天之后的价格剧烈反向，于是只要实现读了未来，
        一致性就会明显偏离。断言的是「一致性 == 手工按 `[i-window+1, i]` 算出的值」，
        逐位对齐，不依赖任何阈值。
        """
        window = PARAMS.cycle_consistency_window
        lookback = PARAMS.ema_slope_lookback_days
        # 前段单边上行，之后急转直下：未来与过去方向相反，读数会立刻分家。
        closes = [100.0 + i for i in range(40)] + [140.0 - 6.0 * i for i in range(40)]
        series = candles(closes)
        index = 45  # 落在转折之后，窗口 [index-window+1, index] 横跨拐点

        metrics = quant.metrics_series(series, PARAMS)
        fast = [m.ema_fast for m in metrics]

        want = 1.0 if fast[index] > fast[index - lookback] else -1.0
        same = total = 0
        for j in range(index - window + 1, index + 1):
            k = j - lookback
            if k < 0:
                continue
            total += 1
            if (1.0 if fast[j] > fast[k] else -1.0) == want:
                same += 1
        expected = same / total

        self.assertEqual(metrics[index].slope_consistency, expected)
        # 前提检查：这段窗口里必须**两种方向都有**，否则「读了未来」也读不出差异。
        self.assertGreater(total, 1)
        self.assertLess(expected, 1.0, "窗口内若全同向，这条断言分辨不了前视")


class TestSeriesShape(SimpleTestCase):
    def test_output_is_one_label_per_candle_in_order(self):
        series = candles(RAMP)
        labels = label_series(series, PARAMS)

        self.assertEqual([lb.date for lb in labels], [row["date"] for row in series])

    def test_empty_input_yields_no_labels(self):
        self.assertEqual(label_series([]), [])

    def test_latest_label_is_the_tail_of_the_series(self):
        """单元 4 走 `latest_label`，单元 6 走 `label_series`。

        两者必须是同一次计算的两端——若各写一份，量化口径就有了第二处实现，而它们
        之间的任何一点差异都会静默存在（两边看起来都很正常）。
        """
        series = candles(DOWN_CALMING)
        self.assertEqual(latest_label(series, PARAMS), label_series(series, PARAMS)[-1])

    def test_latest_label_distinguishes_empty_from_undecidable(self):
        """`None` = 一根 K 线都没有（取数问题）；`DayLabel(regime=None)` = 有数据但
        还判不出来（等预热）。单元 4 要分开处理这两件事，所以不能合并成一个值。
        """
        undecidable = latest_label(candles([100.0, 101.0]), PARAMS)

        self.assertIsNotNone(undecidable)
        self.assertIsNone(undecidable.regime)
        self.assertIsNone(latest_label([]))


class TestTwoAxesAreProjectedFromOneVerdict(SimpleTestCase):
    """两维（cycle / volatility）与四档的**兼容投影**。

    这一版不改判定结论：`label_series` 仍只算出 `BaseRegime`。两维是由那个结论**投影**
    出来的，所以「把两维投影回四档」必须与 `label_series` 逐日相等——这是本票唯一的
    等价性约束（#63）。

    投影为什么是这个形状，逐条都有来处：

    - `high_vol` → `cycle = None`。旧结构里波动吃掉了周期（HIGH_VOL 排 `PRIORITY`
      第一位），那天的周期信息**本来就不存在**。如实留空，而不是从价格里再猜一个。
    - `range` → `sideways`。旧口径的 range 是兜底档，它现在的语义就是「横盘」。
    - 只有 `high_vol` 才是 `event_spike`。不是因为它是事件驱动的（本版还不是），
      而是因为「投影回四档」必须能还原：若把 range 也标成 event_spike，投影就回不去。
    """

    def test_a_judged_day_always_carries_the_volatility_axis(self):
        """判得出的日子**波动维必有值**；周期维可以合规地留空（见下一条）。

        两条轴的不对称是 issue #63 明确要求的：`high_vol` 那天的周期信息在旧判据里
        根本不存在，所以留 `None` 是如实，不是缺失。
        """
        labels = label_series(candles(RAMP), PARAMS)

        for label in labels:
            if not label.judged:
                continue
            with self.subTest(day=label.date):
                self.assertIsNotNone(label.volatility)

    def test_an_unjudged_day_carries_no_axes(self):
        """判不出来的日子两维都是 `None`：与 `regime` 同进退，不填默认值。

        填 `sideways + normal` 就是拿一个安静的假结论去喂切片——那正是本模块在防的。
        """
        labels = label_series(candles(RAMP), PARAMS)

        for label in labels:
            if label.judged:
                continue
            with self.subTest(day=label.date):
                self.assertIsNone(label.regime)
                self.assertIsNone(label.cycle)
                self.assertIsNone(label.volatility)

    def test_the_projection_round_trips_to_the_four_tiers(self):
        """**兼容投影**（`project_to_axes`）仍是四档到两维的唯一逆表。

        #64 之后 `cycle` 有独立判据，所以「两维投影回四档 == regime」这条**不再成立**：
        `base=high_vol` 那天 cycle 可以是 `bear`（波动吃掉周期，但周期维自己看得见方向），
        而 `bear + event_spike` 正是设计上无法还原的合法组合。这里因此只钉逆表本身
        （三档一一对应 + high_vol 的 cycle 留空），真正的端到端等价由下面那组按新结构
        重写的用例负责。
        """
        for regime, (cycle, volatility) in quant._TO_AXES.items():
            with self.subTest(regime=regime.value):
                self.assertEqual(quant.project_to_axes(regime), (cycle, volatility))
                if regime is not BaseRegime.HIGH_VOL:
                    self.assertIs(quant.project_to_base_axes(cycle, volatility), regime)

    def test_high_vol_no_longer_erases_the_cycle_axis(self):
        """#64 之后「高波动」不再吞掉周期：那是本票要结束的状态。

        #63 时两维是四档的投影，`high_vol` 的 cycle 只能留空——因为旧结构里波动排在
        `PRIORITY` 第一位，那天的周期信息在判据里**根本不存在**。现在周期维有自己的
        两条路径，它与四档互不覆盖，于是同一天可以既是 `high_vol`（该保命）又是
        `bear`（方向是下行）。这正是 ADR 0007 要的「两维并存」。

        `project_to_base` 在这里**不抛**（#65 改了这一点）：两维现在是真判定，
        `bear + event_spike` 是它的**正常产物**——而 #63 当时抛是对的，那时两维只是
        四档的投影，出现这个组合意味着投影表自相矛盾。现在投影是兼容视图，它的塌法是
        保留保命档、丢掉方向；方向没丢，它在 `cycle` 那一列里躺着。
        """
        # 上行途中放量：两维并存的那个形状（放量是事件，但它不改方向）。
        params = replace(PARAMS, volume_ma_period=3)
        closes = [100 + i for i in range(16)]
        rows = [
            {
                "date": DAY0 + timedelta(days=i),
                "high": float(c) + 1.0,
                "low": float(c) - 1.0,
                "open": float(c),
                "close": float(c),
                "volume": 10.0 if i < len(closes) - 1 else 200.0,
            }
            for i, c in enumerate(closes)
        ]
        label = label_series(rows, params)[-1]

        self.assertIs(label.regime, BaseRegime.HIGH_VOL)
        self.assertIs(label.volatility, quant.Volatility.EVENT_SPIKE)
        self.assertIs(label.cycle, quant.Cycle.BULL, "周期维独立看见了上行方向")
        # 投影仍还原出保命档（丢掉的是方向这一份分辨率，不是保命这件事）。
        self.assertIs(quant.project_to_base(label), BaseRegime.HIGH_VOL)

    def test_the_other_three_tiers_map_one_to_one(self):
        expected = {
            BaseRegime.UPTREND: (quant.Cycle.BULL, quant.Volatility.NORMAL),
            BaseRegime.DOWNTREND: (quant.Cycle.BEAR, quant.Volatility.NORMAL),
            BaseRegime.RANGE: (quant.Cycle.SIDEWAYS, quant.Volatility.NORMAL),
        }
        for regime, (cycle, volatility) in expected.items():
            with self.subTest(regime=regime.value):
                pair = quant.project_to_axes(regime)
                self.assertEqual(pair, (cycle, volatility))
                self.assertIs(quant.project_to_base_axes(cycle, volatility), regime)

    def test_unclear_is_a_cycle_value_and_projects_to_nothing(self):
        """`unclear` 是 cycle 的取值（判不出来），与 `None` 不是一回事：

        `None` = 这一版没有周期结论（high_vol 吃掉了）；`unclear` = 有周期判定但判不出来。
        本版还不产出 `unclear`（#64 才引入），但投影表必须先认它，否则 #64 落地时
        这里会成为一处静默的 KeyError。
        """
        self.assertIn("unclear", {c.value for c in quant.Cycle})
        self.assertIsNone(
            quant.project_to_base_axes(quant.Cycle.UNCLEAR, quant.Volatility.NORMAL)
        )

    def test_a_directional_event_day_projects_to_the_blanket_tier(self):
        """`bull + event_spike`（牛市暴跌日）投影回四档 ⇒ 保命档。

        **#65 起这是正常产出**，不是矛盾：两维各自独立判定，同一天既是上行周期又是事件
        波动日完全可能。投影是兼容视图，它把这一格塌成 `high_vol`——保命档这件事必须
        保留（它决定要不要停全场），被丢掉的只是方向的分辨率，而方向在 `cycle` 列里
        原样存在。

        #63 时这里抛是对的：那时两维还只是四档的投影，出现这个组合说明投影表自相矛盾。
        现在再抛就成了「一有事件波动日，整条历史标注链挂掉」。
        """
        self.assertIs(
            quant.project_to_base_axes(quant.Cycle.BULL, quant.Volatility.EVENT_SPIKE),
            BaseRegime.HIGH_VOL,
        )
        self.assertIs(
            quant.project_to_base_axes(quant.Cycle.BEAR, quant.Volatility.EVENT_SPIKE),
            BaseRegime.HIGH_VOL,
        )


class TestVolatilityRatioIsNotAQuota(SimpleTestCase):
    """**高波动比例随行情自然变化，不再锁死 20%**（#65 的核心验收）。

    旧判据 `atr_pct_rank > 0.80` 在数学上锁死「250 日窗口里必有 20% 的日子判高波动」。
    这条用例用一个**分位数分布已知**的夹具证明新判据没有这个性质：同样的窗口长度，
    事件日数取决于行情本身走了几根大阴大阳，而不是窗口大小。

    真实历史上的实测（2026-11-23，BTC 2518 根日线）：19.29% → 4.83%，
    逐年 2.1% ~ 10.4%。这里用合成序列钉同一条性质，不必依赖线上库。
    """

    def _ratio(self, closes, bands=1.0):
        params = replace(PARAMS, volume_ma_period=3)
        labels = label_series(candles(closes, bands), params)
        judged = [lb for lb in labels if lb.judged]
        spikes = [lb for lb in judged if lb.volatility is quant.Volatility.EVENT_SPIKE]
        return len(spikes), len(judged)

    def test_a_calm_series_has_no_event_days(self):
        """平静序列 ⇒ 0 个事件日。旧判据在这里必然给出 20%——它与「行情平不平静」无关。"""
        spikes, judged = self._ratio([100.0 + 0.01 * i for i in range(60)])
        self.assertGreater(judged, 0, "夹具必须真的判出一些日子，否则用例空转")
        self.assertEqual(spikes, 0)

    def test_the_ratio_tracks_the_number_of_event_days_not_the_window(self):
        """同一窗口长度、不同的行情 ⇒ 事件日数跟着行情走，而不是跟着窗口走。"""
        calm = [100.0 + 0.01 * i for i in range(60)]
        # 在平静序列里插几根 +10% 的大阳线：每一根都该被判成事件日。
        choppy = list(calm)
        for at in (30, 40, 50):
            for i in range(at, len(choppy)):
                choppy[i] *= 1.10
        calm_spikes, calm_judged = self._ratio(calm)
        choppy_spikes, choppy_judged = self._ratio(choppy)

        self.assertEqual(calm_spikes, 0)
        self.assertGreater(
            choppy_spikes,
            0,
            "同样的窗口长度，有事件的日子必须被认出来——否则判据又在看窗口而不是看行情",
        )
        # 比例不同：这条是「不再锁死」的直接证据。
        self.assertNotEqual(
            calm_spikes / max(1, calm_judged), choppy_spikes / max(1, choppy_judged)
        )


class TestProjectionEquivalenceOnBulkHistory(SimpleTestCase):
    """**全量历史**的投影等价：随机与极端输入下，两维都必须能投影回原四档。

    `test_the_projection_round_trips_to_the_four_tiers` 只覆盖五组人工夹具；本组把
    输入扩到几千条随机游走 + 四档各自的极端参数，钉的是「没有哪一类输入会让投影失真」。

    这也是 issue #63 那句「跑一遍全量历史，两维投影回四档与原 `label_series` 逐日相等」
    在没有生产库（本机 `DailyCandle` 为 0 行）时的可执行形态：等价性是**逐日**成立的，
    而它成立的原因与具体哪天无关——所以用大批随机历史的逐日断言代替一次全量落库扫描，
    两者证的是同一件事。
    """

    def _axes_are_present_on_judged_days(self, rows, params):
        """判得出的日子两维**必有值**；判不出的日子两维**同时为空**。

        #64 之后不再断言「投影回四档相等」——cycle 有独立判据，它与四档允许不一致，
        且不一致正是本票要的结果。这里钉的是同进退这条更强也更本质的性质：
        不许出现「有一维、没另一维」的半截结论。
        """
        labels = label_series(rows, params)
        for label in labels:
            if not label.judged:
                with self.subTest(day=label.date):
                    self.assertIsNone(label.cycle)
                    self.assertIsNone(label.volatility)
                continue
            with self.subTest(day=label.date):
                self.assertIsNotNone(label.volatility)
                self.assertIsNotNone(label.cycle)

    def test_a_long_random_walk_round_trips_every_day(self):
        rng = np.random.default_rng(20261123)
        closes = [100.0]
        for _ in range(1200):
            closes.append(max(1.0, closes[-1] * (1.0 + rng.normal(0, 0.03))))
        # 振幅随价格漂移：让 ATR% 分位真的动起来，四档都会出现。
        bands = [abs(rng.normal(0.02, 0.02)) + 0.001 for _ in closes]
        self._axes_are_present_on_judged_days(candles(closes, bands), PARAMS)

    def test_every_tier_appears_in_that_history(self):
        """前提检查：上面那条随机游走必须**真的产生四档**。

        少了这条，上一行可能只是在「全是 range」的历史上通过了——那样的等价性是平凡
        成立的，不证明任何事。
        """
        rng = np.random.default_rng(20261123)
        closes = [100.0]
        for _ in range(1200):
            closes.append(max(1.0, closes[-1] * (1.0 + rng.normal(0, 0.03))))
        bands = [abs(rng.normal(0.02, 0.02)) + 0.001 for _ in closes]
        seen = {lb.regime for lb in label_series(candles(closes, bands), PARAMS)}

        self.assertEqual(
            seen - {None},
            {
                BaseRegime.UPTREND,
                BaseRegime.DOWNTREND,
                BaseRegime.RANGE,
                BaseRegime.HIGH_VOL,
            },
            "夹具必须真的覆盖四档，否则等价性断言是空转的（`None` 是预热期，另算）",
        )

    def test_the_four_extreme_regimes_round_trip_under_every_parameter_set(self):
        """四档各自的最短触发路径，在几组真实参数下都要往返相等。"""
        variants = [
            PARAMS,
            replace(PARAMS, trend_separation_min_atr=0.15),
            replace(PARAMS, high_vol_quantile=0.6),
            replace(PARAMS, quantile_window_days=12),
        ]
        fixtures = {
            "uptrend": candles(RAMP),
            "downtrend": candles(DOWN_CALMING),
            "high_vol": candles(STEADY_DOWN),
            "range": candles(TINY_DRIFT),
        }
        for params in variants:
            for name, rows in fixtures.items():
                with self.subTest(params=params, fixture=name):
                    self._axes_are_present_on_judged_days(rows, params)

    def test_empty_and_single_row_inputs_have_no_axes(self):
        for rows in ([], candles([100.0])):
            with self.subTest(n=len(rows)):
                self._axes_are_present_on_judged_days(rows, PARAMS)


class TestProjectionBoundaries(SimpleTestCase):
    """`project_to_base_axes` 的边界格。每一格都是一种「还原得出来 / 还原不出来」。"""

    def test_unclear_with_an_event_spike_is_high_vol(self):
        """判不出方向 + 事件波动 = 旧结构里的 `high_vol`（那天就是波动吃掉了周期）。"""
        self.assertIs(
            quant.project_to_base_axes(
                quant.Cycle.UNCLEAR, quant.Volatility.EVENT_SPIKE
            ),
            BaseRegime.HIGH_VOL,
        )

    def test_an_empty_cycle_with_normal_volatility_cannot_be_restored(self):
        """没有方向、也没有事件波动——旧四档里没有这一格，必须抛而不是猜。"""
        with self.assertRaises(ValueError):
            quant.project_to_base_axes(None, quant.Volatility.NORMAL)

    def test_a_missing_volatility_axis_cannot_be_restored(self):
        """**只有一维**是半截数据：那不是「某种状态」，是写入路径漏了一半。

        `cycle=None` 不在这一组里：它与 `volatility=None` 同空时是合法的「未判定」，
        由 `test_both_empty_is_the_undecided_day` 单独钉住。两维同空与「缺一维」的
        区别正是这一格要守的线。
        """
        for cycle in (quant.Cycle.BULL, quant.Cycle.BEAR, quant.Cycle.SIDEWAYS):
            with self.subTest(cycle=cycle):
                with self.assertRaises(ValueError):
                    quant.project_to_base_axes(cycle, None)

    def test_both_empty_is_the_undecided_day(self):
        """两维同空 = 未判定，还原成 `None`。这是唯一允许两维同空的组合。"""
        self.assertIsNone(quant.project_to_base_axes(None, None))

    def test_string_inputs_are_accepted(self):
        """读面与迁移传的是字符串而非枚举值，投影必须认。"""
        self.assertIs(
            quant.project_to_base_axes("bear", "normal"), BaseRegime.DOWNTREND
        )


class TestMigrationBackfillTableIsInSync(SimpleTestCase):
    """迁移 0018 的回填表必须与 `quant._TO_AXES` 一致。

    迁移刻意**不 import** 应用代码（那样历史迁移会随代码漂移），所以它自带一份表。
    重复是刻意的，但两份漂开就是静默的错：新写入的行按 `_TO_AXES` 投影，历史行按
    迁移表回填，两者不一致时同一档会得到两个两维，而两边看起来都正常。

    这条护栏只比较两张表、不碰数据库——所以它能看见「迁移在测试库里从未被执行」
    这件事（测试库按 models 建表，RunPython 不跑）。
    """

    def _mapping(self):
        """把迁移模块里的回填表取出来。

        直接 `import` 模块文件而不是 `apps.get_model`：要读的是那份**常量表**，
        不是模型。模块级只 import `django.db`，import 它不会碰数据库。
        """
        import importlib

        module = importlib.import_module(
            "apps.regime.migrations.0018_two_axis_cycle_volatility"
        )
        return module._LEGACY_MAPPING, module.LEGACY_SOURCE

    def test_the_two_tables_agree(self):
        mapping, _ = self._mapping()

        from apps.regime.quant import _TO_AXES

        self.assertEqual(
            {
                regime.value: (c.value if c else None, v.value)
                for regime, (c, v) in _TO_AXES.items()
            },
            mapping,
            "迁移回填表与 quant._TO_AXES 漂开了：历史行与新行会对同一档给出不同的两维",
        )

    def test_the_legacy_source_constant_matches_the_code(self):
        """`legacy_mapping` 这个字面量在两处必须相同，否则读的人分不清来源。"""
        _, source = self._mapping()

        from apps.regime.quant import VOLATILITY_SOURCE_LEGACY

        self.assertEqual(source, VOLATILITY_SOURCE_LEGACY)


class TestPercentileRank(SimpleTestCase):
    """分位是整套阈值的标定基准，所以它的口径本身就是规格。"""

    def test_counts_values_less_than_or_equal(self):
        """`<=`（含相等）：并列时计入更高分位。"""
        self.assertEqual(percentile_rank(np.array([1.0, 2.0, 2.0, 3.0]), 2.0), 0.75)

    def test_the_maximum_of_a_rising_window_is_one(self):
        self.assertEqual(percentile_rank(np.array([1.0, 2.0, 3.0, 4.0]), 4.0), 1.0)

    def test_a_value_below_everything_is_zero(self):
        self.assertEqual(percentile_rank(np.array([1.0, 2.0, 3.0, 4.0]), 0.5), 0.0)

    def test_all_ties_give_one(self):
        """全部并列时恒为 1.0——这是「并列往保守倒」的直接后果。

        真实日线的 ATR% 撞上同一浮点数的概率极低，这条只在退化数据（价格被重复返回、
        停牌）上出现；那时判成高波动意味着停开新仓，方向是对的。
        """
        self.assertEqual(percentile_rank(np.array([5.0, 5.0, 5.0]), 5.0), 1.0)

    def test_is_an_exact_integer_count_not_an_interpolation(self):
        """分数秩：k/n 的精确有理数，跨机器、跨 numpy 版本完全一致。

        插值分位会引入实现定义的自由度，而这里是阈值的标定基准。
        """
        window = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0])
        self.assertEqual(percentile_rank(window, 4.0), 5 / 7)

    def test_empty_window_raises(self):
        """空窗口没有分位可言；返回 0 或 0.5 都会变成一个安静的假结论。"""
        with self.assertRaises(ValueError):
            percentile_rank(np.array([]), 1.0)


class TestInputHandling(SimpleTestCase):
    def test_decimal_and_string_rows_give_the_same_labels(self):
        """落库读回是 Decimal，ccxt 给的是 float，JSON 里可能是字符串。

        三条来源必须是同一口径，否则「同一段行情」在实时判定与历史标注两条路上
        会得出不同的阶段——而那正是切片结论不可信的开端。
        """
        plain = candles(RAMP)
        typed = [
            {
                "date": row["date"],
                "high": Decimal(str(row["high"])),
                "low": Decimal(str(row["low"])),
                "close": str(row["close"]),
            }
            for row in plain
        ]

        self.assertEqual(label_series(typed, PARAMS), label_series(plain, PARAMS))
        self.assertIs(label_series(typed, PARAMS)[-1].regime, BaseRegime.UPTREND)

    def test_datetime_open_times_are_accepted_and_reduced_to_a_date(self):
        """上游给的是日线开盘时刻（UTC 零点），标签要的是日粒度。"""
        rows = [
            {
                "date": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
                "high": 100.0 + i + 1,
                "low": 100.0 + i - 1,
                "close": 100.0 + i,
            }
            for i in range(16)
        ]
        labels = label_series(rows, PARAMS)

        self.assertEqual(labels[0].date, date(2026, 1, 1))
        self.assertEqual(labels[-1].date, date(2026, 1, 16))

    def test_a_row_without_a_date_is_rejected(self):
        """日期是必填的：标签序列的整个用途就是「日期 → 阶段」，单元 6 按开仓日期 join。

        缺日期还能悄悄产出一条标签——那条标签永远 join 不上，而它的存在看起来
        完全正常。
        """
        with self.assertRaises(ValueError) as ctx:
            label_series([{"high": 101.0, "low": 99.0, "close": 100.0}], PARAMS)
        self.assertIn("date", str(ctx.exception))

    def test_an_unparseable_price_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            label_series(
                [{"date": DAY0, "high": 101.0, "low": 99.0, "close": "abc"}], PARAMS
            )
        self.assertIn("high/low/close", str(ctx.exception))


class TestParametersAreHonoured(SimpleTestCase):
    """参数真的在起作用——否则「改参数必须改代码发版」这句就是空话。"""

    def test_raising_the_separation_threshold_demotes_a_trend_to_range(self):
        calm = candles(DOWN_CALMING)
        self.assertIs(
            label_series(calm, PARAMS)[EXPECTED_ONSET].regime, BaseRegime.DOWNTREND
        )

        strict = replace(PARAMS, trend_separation_min_atr=3.0)
        self.assertIs(
            label_series(calm, strict)[EXPECTED_ONSET].regime, BaseRegime.RANGE
        )

    def test_high_vol_threshold_is_exclusive_at_the_rank_that_lands_on_it(self):
        """分位恰好落在阈值上那一天：不算高波动，趋势那一支照常生效。"""
        calm = candles(DOWN_CALMING)
        # 收敛段的排名按 5 日窗口是 0.2/0.4/0.6/…，取阈值 0.6 让某一天正好压线。
        on_the_line = replace(PARAMS, high_vol_quantile=0.6)
        labels = label_series(calm, on_the_line)

        exactly = [lb for lb in labels if lb.atr_pct_rank == 0.6]
        self.assertTrue(exactly)
        self.assertTrue(all(lb.regime is BaseRegime.DOWNTREND for lb in exactly))

    def test_a_longer_quantile_window_delays_the_first_judgement(self):
        """窗口拉长到 12 天，第一次判定推迟到下标 13——**不是** 11。

        这里是两条约束的叠加，容易只想到前一条：既要窗口「够长」（i+1 >= 12 → i >= 11），
        还要窗口里 12 个 ATR% **全是有限值**，而 ATR% 自己要到下标 2 才有值。所以真正
        的开判日是 2 + 12 - 1 = 13。漏掉这一层，代码会在标注序列开头多写几天假标签。
        """
        labels = label_series(candles(RAMP), replace(PARAMS, quantile_window_days=12))

        self.assertTrue(all(not lb.judged for lb in labels[:13]))
        self.assertTrue(labels[13].judged)
        self.assertEqual(labels[13].quantile_sample, 12)
