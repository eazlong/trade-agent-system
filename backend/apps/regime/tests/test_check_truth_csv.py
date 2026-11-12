"""`check_truth_csv` 这道闸：**写之前**把问题说清，不写库、不评价标得对不对。

这一组要钉的是一件事——**四类问题里哪几类挡录入**。判据不是「严不严」，而是
「录进去之后会不会变成一份看起来正常、实际不能用的真值」：

- 格式错 / 自相矛盾 / 与库冲突 ⇒ **挡**。它们要么让文件缺段，要么让标的那天白标
  （冲突日退出分母），两种都是「录进去了、没算数」。
- 越界 ⇒ **不挡**。CONTEXT.md 第 164 条明写落在算法够不着的时期里的天会如实进
  `missing`。把它做成硬错误会逼人删掉自己确信的标注。

第二条特别值得一条用例：它是这个命令**唯一**敢放行的那一类，而放行的理由写在
CONTEXT.md 里，不是「懒」。

第三条要钉的是**这个命令一个字都不说算法结论**：`--show-coverage` 也只报「日线范围
里/外」，因为第 164 条要求标注发生在算法输出可见之前。判定这一层有没有偷偷回显
`high_vol` / `atr_pct_rank` 之类，是这一组最该防的回归。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from apps.regime import truth
from apps.regime.management.commands import check_truth_csv as mod
from apps.regime.models import DailyCandle
from apps.regime.quant import BaseRegime

SYMBOL = "BTC/USDT"


def write_csv(
    tmp: Path, body: str, *, header: str = "日期,档位（待填）,备注（待填）"
) -> Path:
    """在测试目录里落一份 CSV。用 `utf-8-sig` 与模板一致（Excel 会带 BOM）。"""
    path = tmp / "labels.csv"
    path.write_text(f"{header}\n{body}", encoding="utf-8-sig")
    return path


class TestParsing(TestCase):
    def setUp(self):
        self.tmp = Path(self._testMethodName + "_tmp")
        self.tmp.mkdir(exist_ok=True)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for child in self.tmp.glob("*"):
            child.unlink()
        self.tmp.rmdir()

    def test_a_blank_regime_is_not_an_error(self):
        """**档位空 = 没标**。这是模板的正常状态，不是问题。

        模板预填了全部日期（只给价格），人只在自己确信的日子填档位。把空档位报成错会在
        空白模板上刷出几百条假错误，而假错误会训练人整个跳过这个命令——它存在的意义
        恰恰是让人肯看它。
        """
        path = write_csv(
            self.tmp,
            "2020-03-12,高波动,312\n2020-03-13,,\n2020-03-14,,\n",
        )
        rows, problems = mod.parse(path)

        self.assertEqual(len(rows), 1, "只有填了档位的那一行进结果")
        self.assertEqual(rows[0].regime, BaseRegime.HIGH_VOL)
        self.assertEqual(problems, [], "空档位不该报任何问题")

    def test_a_regime_without_a_date_is_an_error(self):
        """反过来的组合才是错：有档位没日期，不知道该落到哪一天。"""
        path = write_csv(self.tmp, "2020-03-12,高波动,312\n,高波动,没日期\n")
        _, problems = mod.parse(path)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].kind, "格式")
        self.assertIn("日期解析不了", problems[0].detail)

    def test_both_chinese_names_and_slugs_are_accepted(self):
        path = write_csv(
            self.tmp,
            "2020-03-12,高波动,a\n2020-03-13,high_vol,b\n"
            "2020-03-14,箱体震荡,c\n2020-03-15,range,d\n",
        )
        rows, problems = mod.parse(path)

        self.assertEqual(problems, [])
        self.assertEqual(
            [r.regime for r in rows],
            [
                BaseRegime.HIGH_VOL,
                BaseRegime.HIGH_VOL,
                BaseRegime.RANGE,
                BaseRegime.RANGE,
            ],
        )

    def test_a_typo_in_the_regime_name_is_reported_with_the_line(self):
        """`极端波动` 不在四档里——它是人话，但不是这套枚举的词。

        报错要带行号：几百行的表里，「有个档位写错了」这种话找不到落点。
        """
        path = write_csv(self.tmp, "2020-03-12,高波动,a\n2020-03-13,极端波动,b\n")
        _, problems = mod.parse(path)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].line, 3, "表头是第 1 行，这条在第 3 行")
        self.assertIn("极端波动", problems[0].detail)
        self.assertIn("高波动/下行趋势", problems[0].detail, "要列出现行词表")

    def test_a_bom_in_the_header_does_not_break_the_date_column(self):
        """Excel 另存出来的 CSV 带 BOM。不剥掉的话第一列表头变成 `\\ufeff日期`。"""
        path = write_csv(self.tmp, "2020-03-12,高波动,a\n")
        rows, problems = mod.parse(path)

        self.assertEqual(problems, [])
        self.assertEqual(len(rows), 1)

    def test_a_missing_regime_column_is_a_file_level_problem(self):
        path = write_csv(self.tmp, "2020-03-12,a\n", header="日期,备注")
        rows, problems = mod.parse(path)

        self.assertEqual(rows, [])
        self.assertEqual(len(problems), 1)
        self.assertIsNone(problems[0].line, "这是整份文件的问题，不挂在某一行上")
        self.assertIn("档位", problems[0].detail)


class TestMerging(TestCase):
    def _row(self, line, day, regime, note=""):
        return mod.Row(line=line, day=date.fromisoformat(day), regime=regime, note=note)

    def test_adjacent_days_of_the_same_regime_become_one_interval(self):
        """合并只做一件事：相邻 + 同档位。它不改变语义（`day_regimes` 按日展开）。"""
        rows = [
            self._row(2, "2020-03-12", BaseRegime.HIGH_VOL, "312"),
            self._row(3, "2020-03-13", BaseRegime.HIGH_VOL),
            self._row(4, "2020-03-14", BaseRegime.HIGH_VOL),
        ]
        spans = mod.merge(rows)

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].start, date(2020, 3, 12))
        self.assertEqual(spans[0].end, date(2020, 3, 14))
        self.assertEqual(spans[0].days, 3)
        self.assertEqual(spans[0].note, "312", "备注取这一段第一天的")

    def test_a_gap_breaks_the_interval(self):
        rows = [
            self._row(2, "2020-03-12", BaseRegime.HIGH_VOL),
            self._row(3, "2020-03-14", BaseRegime.HIGH_VOL),
        ]
        self.assertEqual(len(mod.merge(rows)), 2, "中间缺一天就是两段")

    def test_a_regime_change_breaks_the_interval(self):
        rows = [
            self._row(2, "2020-03-12", BaseRegime.HIGH_VOL),
            self._row(3, "2020-03-13", BaseRegime.DOWNTREND),
        ]
        self.assertEqual(len(mod.merge(rows)), 2)

    def test_the_same_day_twice_with_one_regime_collapses_to_one_day(self):
        """同一天同档位标两次 = 标重了，互相佐证。

        合出来是**两段各 1 天**而不是一段 2 天：`merge` 的合并条件是「相邻且同档位」，
        而同一个日期与自己不算相邻。这没有语义后果——`truth.day_regimes` 按日展开，
        两段各 1 天与一段 2 天覆盖的都是那一天。所以这里钉的是「覆盖的天数不变」，
        不是「段数最少」。
        """
        rows = [
            self._row(2, "2020-03-12", BaseRegime.HIGH_VOL),
            self._row(3, "2020-03-12", BaseRegime.HIGH_VOL),
        ]
        spans = mod.merge(rows)
        self.assertEqual(len(truth.day_regimes(spans)), 1, "覆盖的日集合只有一天")
        self.assertEqual(sum(s.days for s in spans), 2, "两段各 1 天")


class TestSelfConflicts(TestCase):
    def _row(self, line, day, regime):
        return mod.Row(line=line, day=date.fromisoformat(day), regime=regime, note="")

    def test_one_day_with_two_regimes_is_a_conflict_with_both_lines(self):
        """那些天会成为冲突日、退出分母——**等于白标**，所以必须挡住。"""
        rows = [
            self._row(2, "2020-03-12", BaseRegime.HIGH_VOL),
            self._row(5, "2020-03-12", BaseRegime.DOWNTREND),
        ]
        problems = mod.self_conflicts(rows)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].kind, "自相矛盾")
        self.assertIn("第 2 行", problems[0].detail)
        self.assertIn("第 5 行", problems[0].detail)
        self.assertTrue(problems[0].blocking)

    def test_the_same_day_with_the_same_regime_is_not_a_conflict(self):
        rows = [
            self._row(2, "2020-03-12", BaseRegime.HIGH_VOL),
            self._row(3, "2020-03-12", BaseRegime.HIGH_VOL),
        ]
        self.assertEqual(mod.self_conflicts(rows), [])


class TestWhatBlocksAndWhatDoesNot(TestCase):
    def test_coverage_gaps_are_advisory_not_blocking(self):
        """第 164 条：落在算法够不着的时期里的天会如实进 `missing`，不是错。

        这条把这个命令**唯一**敢放行的那一类钉住。把它改成 blocking 会逼人删掉自己
        确信的标注——而那些恰好是最有价值的那部分。
        """
        problem = mod.Problem(None, "覆盖", "越界", blocking=False)
        self.assertFalse(problem.blocking)
        self.assertIn("不阻止录入", problem.render())

    def test_format_errors_block_by_default(self):
        """默认就是 blocking：新增一类检查时，忘了写 `blocking=False` 应该偏严。"""
        self.assertTrue(mod.Problem(3, "格式", "x").blocking)
        self.assertNotIn("不阻止录入", mod.Problem(3, "格式", "x").render())

    def test_against_db_treats_a_same_regime_overlap_as_corroboration(self):
        """同档位重叠是互相佐证，不同档位才是冲突——与 `truth.conflicting_days` 同一语义。"""
        from apps.regime import truth

        command = mod.Command()
        spans = [
            truth.Interval(
                start=date(2020, 3, 12),
                end=date(2020, 3, 14),
                regime=BaseRegime.HIGH_VOL,
            )
        ]

        existing_same = [
            truth.Interval(
                start=date(2020, 3, 13),
                end=date(2020, 3, 20),
                regime=BaseRegime.HIGH_VOL,
                id=1,
            )
        ]
        existing_differs = [
            truth.Interval(
                start=date(2020, 3, 13),
                end=date(2020, 3, 20),
                regime=BaseRegime.RANGE,
                id=2,
            )
        ]

        from unittest.mock import patch

        with patch("apps.regime.truth_run.live_intervals", return_value=existing_same):
            self.assertEqual(command._against_db(spans), [], "同档位重叠不是冲突")

        with patch(
            "apps.regime.truth_run.live_intervals", return_value=existing_differs
        ):
            problems = command._against_db(spans)
            self.assertEqual(len(problems), 1)
            self.assertEqual(problems[0].kind, "库冲突")
            self.assertIn(
                "2 天", problems[0].detail, "报重叠的天数（03-13、03-14），人才知道代价"
            )


class TestItNeverLeaksAlgorithmOutput(TestCase):
    """这个命令一个字都不说算法结论（第 164 条：标注必须先于算法输出可见）。"""

    def test_the_coverage_check_only_reads_candle_dates(self):
        """`--show-coverage` 只用日线的**日期边界**，不碰 OHLC，更不碰 `label_series`。

        这条是防回归的：将来有人想「顺便报一下这段一致率」，那一步会把算法答案递到
        标注者眼前，而整个「盲标」的正当性就没了。
        """
        import inspect

        source = inspect.getsource(mod.Command._coverage)

        # 这三样是「算法答案」的入口：标签序列、ATR 指标、档位枚举。它们一个都不许出现。
        # 不检查裸词 "regime"：模块自身的 import 路径里就带着 `apps.regime`，那会让这条
        # 断言永远失败，于是它要么被删掉、要么被放宽到没意义——正是要避免的退化。
        self.assertNotIn("label_series", source)
        self.assertNotIn("atr", source.lower())
        self.assertNotIn("high_vol", source)
        self.assertNotIn("BaseRegime", source)
        self.assertIn('values_list("date"', source, "只读日期列")

    def test_the_command_text_says_it_does_not_judge_correctness(self):
        """人读到的最后一句话必须把边界说清，否则「0 个问题」会被读成「标得对」。"""
        source = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertIn("不判断标得对不对", source)


class TestEndToEnd(TestCase):
    """真的走一遍 `handle()`：解析 → 冲突 → 合并 → 报告。"""

    def setUp(self):
        self.tmp = Path(self._testMethodName + "_e2e")
        self.tmp.mkdir(exist_ok=True)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for child in self.tmp.glob("*"):
            child.unlink()
        self.tmp.rmdir()

    def _run(self, path, *extra):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(
            "check_truth_csv", str(path), *extra, stdout=out, stderr=StringIO()
        )
        return out.getvalue()

    def test_a_clean_file_reports_the_intervals_it_would_write(self):
        path = write_csv(self.tmp, "2020-03-12,高波动,312\n2020-03-13,高波动,312\n")
        output = self._run(path)

        self.assertIn("解析到 2 行已标注日", output)
        self.assertIn("合并为 1 段区间", output)
        self.assertIn("2020-03-12 → 2020-03-13", output)
        self.assertIn("高波动", output)

    def test_a_blocking_problem_exits_nonzero(self):
        from django.core.management.base import CommandError

        path = write_csv(self.tmp, "2020-03-12,高波动,a\n2020-03-13,不存在的档位,b\n")
        with self.assertRaises(CommandError):
            self._run(path)

    def test_an_empty_candle_table_is_reported_not_silently_skipped(self):
        """日线表为空时 `--show-coverage` 要如实说「无法判断越界」，而不是静默通过。

        与「越界不挡」不同：**这条挡**。一个空日线表意味着连比对基准都没有，
        此时录进去的任何标注都算不出一致率——那是配置问题，不是标注问题。
        """
        from django.core.management.base import CommandError

        path = write_csv(self.tmp, "2019-01-01,箱体震荡,早期\n")
        with self.assertRaises(CommandError):
            self._run(path, "--show-coverage")


class TestCandleBoundsComeFromTheDatabase(TestCase):
    def test_the_bounds_are_the_actual_candle_range(self):
        for day, close in ((date(2020, 1, 1), "7000"), (date(2026, 11, 11), "74000")):
            DailyCandle.objects.create(
                symbol=SYMBOL,
                date=day,
                open_time=__import__("datetime").datetime.combine(
                    day,
                    __import__("datetime").time(0, 0),
                    tzinfo=__import__("datetime").timezone.utc,
                ),
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal("1"),
            )

        command = mod.Command()
        problems = command._coverage(
            [mod.Row(line=2, day=date(2019, 6, 1), regime=BaseRegime.RANGE, note="")]
        )

        self.assertEqual(len(problems), 1)
        self.assertIn("2020-01-01", problems[0].detail)
        self.assertFalse(problems[0].blocking)
