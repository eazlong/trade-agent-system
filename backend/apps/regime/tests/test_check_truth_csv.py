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

from django.test import SimpleTestCase, TestCase

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

    def test_a_slash_date_in_the_excel_style_is_accepted(self):
        """`2020/1/14` 与 `2020-01-14` 是同一个日子，都要认。

        Excel 把 `2020-03-12` 显示成 `2020/3/12` 是它自己的默认日期格式，手工敲也一样。
        只认横杠一种写法会把「格式不对」变成最强的假警报——实测一份 142 行的表**全部**
        因为这一点被拒，而人第一反应是「这工具不能用」，不是「我去改一下日期」。
        """
        path = write_csv(
            self.tmp,
            "2020/1/14,上行趋势,a\n2021/05/19,高波动,b\n2022/11/9,高波动,c\n",
        )
        rows, problems = mod.parse(path)

        self.assertEqual(problems, [], "两种写法都该认")
        self.assertEqual(
            [r.day for r in rows],
            [date(2020, 1, 14), date(2021, 5, 19), date(2022, 11, 9)],
            "不补零的月份/日期要正确解析，不能被当成非法",
        )

    def test_only_the_year_first_form_is_accepted(self):
        """`3/4/2020` 这种歧义写法**不认**，报错而不是猜。

        美式 `3/4` 是 3 月 4 日、欧式是 4 月 3 日。猜错的日子会变成一条假真值，
        而假真值比报错难查得多——它看起来完全正常，只是把不同的一天记成了另一档。
        所以这里只认年在前面的写法，歧义的一律拒绝。
        """
        path = write_csv(self.tmp, "3/4/2020,高波动,a\n")
        _, problems = mod.parse(path)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].kind, "格式")
        self.assertIn("日期解析不了", problems[0].detail)
        self.assertIn("2020-03-12", problems[0].detail, "报错要给出可用的例子")

    def test_an_impossible_date_is_rejected(self):
        """`2020-02-30` 不存在。`strptime` 自己会拒，这里钉住那条路不被吞掉。"""
        path = write_csv(self.tmp, "2020-02-30,高波动,a\n")
        _, problems = mod.parse(path)
        self.assertEqual(len(problems), 1)

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
        """那些天会成为冲突日、退出分母——**等于白标**，所以必须挡住。

        **用两个非叠加档位**（下行 + 箱体）造冲突：高波动是叠加层，它与另一档并存
        是「这段在跌、其中某天崩了」，不是自相矛盾。
        """
        rows = [
            self._row(2, "2020-03-12", BaseRegime.DOWNTREND),
            self._row(5, "2020-03-12", BaseRegime.RANGE),
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


class TestTheAiTidyBranch(TestCase):
    """`--ai-tidy`：原始材料 → AI 转写 → **同一条检查链**。

    这一组钉的是一件事：**它没有第二条判据**。转写出来的 CSV 必须走 `parse_text`，于是
    四类问题、合并规则、blocking 的划分全部与普通 CSV 路径逐字相同。任何一处差异都会
    让「同一个材料在两处得出两个结论」，而两边各自看都自洽。
    """

    def setUp(self):
        self.tmp = Path(self._testMethodName + "_ai")
        self.tmp.mkdir(exist_ok=True)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for child in self.tmp.glob("*"):
            child.unlink()
        self.tmp.rmdir()

    def _raw(self, body: str) -> Path:
        path = self.tmp / "notes.txt"
        path.write_text(body, encoding="utf-8")
        return path

    def _run(self, path, *extra):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(
            "check_truth_csv", str(path), *extra, stdout=out, stderr=StringIO()
        )
        return out.getvalue()

    def _stub(self, payload: dict):
        """把 `tidy_raw` 换成「用生产校验器 + 生产渲染器造出来的那一个结果」。

        不手搓 `TidyOutcome`：手搓的话，「命令用的是不是真的那条链」就没有被验证，
        而那正是本组要钉的东西。
        """
        from unittest.mock import patch

        from apps.regime import truth_tidy

        spans = truth_tidy._validate(payload)
        outcome = truth_tidy.TidyOutcome(
            status=truth_tidy.STATUS_OK,
            csv=truth_tidy.render_csv(spans),
            intervals=spans,
        )
        return patch.object(truth_tidy, "tidy_raw", return_value=outcome)

    def test_it_runs_the_same_checks_as_a_plain_csv(self):
        """转写 → 检查 → 合并，三句话都要出现在输出里。

        **区间契约**：一条覆盖两天的高波动区间，转写出来是**一条**，不是两行。
        """
        payload = {
            "intervals": [
                {
                    "start": "2020-03-12",
                    "end": "2020-03-13",
                    "regime": "高波动",
                    "note": "312",
                    "source_text": "3 月 12 日到 13 日：高波动",
                },
            ]
        }
        with self._stub(payload):
            output = self._run(self._raw("原始材料"), "--ai-tidy")

        self.assertIn("AI 只做了转写", output, "边界那句话必须印在人看得到的地方")
        self.assertIn("解析到 1 行已标注日", output, "一条区间就是一行，不按天展开")
        self.assertIn("合并为 1 段区间", output)
        self.assertIn("2020-03-12 → 2020-03-13", output)

    def test_out_writes_the_csv_and_the_report_reads_it_back(self):
        """`--out` 落盘的那份必须能被普通路径**再读一遍**，结论一致。"""
        payload = {
            "intervals": [
                {
                    "start": "2020-03-12",
                    "end": "2020-03-12",
                    "regime": "高波动",
                    "note": "",
                    "source_text": "3/12 高波动",
                }
            ]
        }
        target = self.tmp / "tidied.csv"
        with self._stub(payload):
            output = self._run(self._raw("原始材料"), "--ai-tidy", "--out", str(target))

        self.assertTrue(target.exists())
        self.assertIn(f"已把转写结果写到：{target}", output)

        # 第二条路：把落盘的那份当普通 CSV 再检查一次，结论必须一致。
        self.assertIn("解析到 1 行已标注日", self._run(target))

    def test_an_unrecognised_word_still_blocks_after_transcription(self):
        """AI 照抄了「极端波动」，于是它照旧被 `_parse_regime` 拦下。

        这条是「词表只有一处」的可执行形式：如果 AI 顺手归一成 high_vol，这条用例会绿——
        但绿的是一次**静默改写**，而人以为原文就是这么写的。所以这条用例的价值在于它
        **必须红**：一旦 AI 开始归一，这里会先炸。
        """
        from django.core.management.base import CommandError

        payload = {
            "intervals": [
                {
                    "start": "2020-03-12",
                    "end": "2020-03-12",
                    "regime": "极端波动",
                    "note": "",
                    "source_text": "3/12 极端波动",
                }
            ]
        }
        with self._stub(payload):
            with self.assertRaises(CommandError):
                self._run(self._raw("原始材料"), "--ai-tidy")

    def test_a_failed_tidy_raises_instead_of_passing_with_zero_rows(self):
        """整理失败**必须**是一个错误，不能退化成「0 行已标注日 + 检查通过」。

        后者与本命令存在的意义正好相反：一份整理失败的材料，看起来与一份没填档位的
        模板一模一样。
        """
        from unittest.mock import patch

        from django.core.management.base import CommandError

        from apps.regime import truth_tidy

        failed = truth_tidy.TidyOutcome(
            status=truth_tidy.STATUS_FAILED,
            csv="",
            error="LLM 没有给出可用答复：timeout",
        )
        with patch.object(truth_tidy, "tidy_raw", return_value=failed):
            with self.assertRaises(CommandError) as caught:
                self._run(self._raw("原始材料"), "--ai-tidy")

        self.assertIn("AI 整理失败", str(caught.exception))
        self.assertIn("timeout", str(caught.exception))

    def test_without_the_flag_the_path_is_still_a_csv(self):
        """`--ai-tidy` 缺席时 `path` 仍然是 CSV——这一支逐位不变。"""
        path = write_csv(self.tmp, "2020-03-12,高波动,312\n")
        output = self._run(path)

        self.assertNotIn("AI 只做了转写", output)
        self.assertIn("解析到 1 行已标注日", output)


class TestIntervalTablesAreReportedCorrectly(TestCase):
    """区间表的两个措辞陷阱。都不影响录库，但都会让人读错这份材料。

    第一条来自一次真实材料跑测：32 行区间覆盖 4251 天，而报告写着「原始标注 32 天」——
    读的人会以为这份标注很稀，或者以为 AI 只标了 32 天。

    第二条是同一个跑测里刷出的 435 条冲突：两条各跨半年的区间互相对撞时按天报，
    而它们要人做的是同一个动作。
    """

    def setUp(self):
        self.tmp = Path(self._testMethodName + "_iv")
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

    def _write(self, body: str) -> Path:
        path = self.tmp / "intervals.csv"
        path.write_text(f"起始日期,结束日期,档位,备注\n{body}", encoding="utf-8-sig")
        return path

    def test_the_summary_counts_intervals_not_days(self):
        """「原始标注 N 条区间」——而 N 条区间可以覆盖几千天。"""
        output = self._run(self._write("2020-03-13,2021-04-30,上行趋势,一年期上行\n"))

        self.assertIn("原始标注 1 条区间", output)
        self.assertIn("覆盖 414 天", output)

    def test_a_long_interval_conflict_is_one_problem_not_one_per_day(self):
        """两条各跨半年的区间对撞 = **一条**问题，不是 180 条。

        实测一份 44 行的真实材料按天展开刷出 435 条——那种输出没人会读，而「没人读」
        正是这个命令最怕的失效方式。
        """
        from django.core.management.base import CommandError

        path = self._write(
            "2020-01-01,2020-06-30,下行趋势,半年\n2020-04-01,2020-12-31,箱体震荡,也半年\n"
        )
        with self.assertRaises(CommandError) as caught:
            self._run(path)

        message = str(caught.exception)
        self.assertEqual(message, "1 个问题", "180 天重叠只报一条")

    def test_the_conflict_detail_names_both_lines_without_repeating_the_prefix(self):
        """渲染出来不能是「第 3 行：第 3 行（…）」——detail 要说两条的行号。"""
        from apps.regime.management.commands import check_truth_csv as mod

        rows = [
            mod.Row(2, date(2020, 1, 1), BaseRegime.DOWNTREND, "", date(2020, 6, 30)),
            mod.Row(3, date(2020, 4, 1), BaseRegime.RANGE, "", date(2020, 12, 31)),
        ]
        problems = mod.self_conflicts(rows)

        self.assertEqual(len(problems), 1)
        rendered = problems[0].render()
        self.assertNotIn("第 2 行：第 2 行", rendered, "前缀不该重复")
        self.assertIn("第 2 行", rendered)
        self.assertIn("第 3 行", rendered, "两条的行号都要出现，人才知道改哪一条")

    def test_an_interval_missing_its_end_is_a_format_error(self):
        """少了一端**不补成当天**：那正是 v1 把长区间缩成一天的形状。"""
        from django.core.management.base import CommandError

        path = self._write("2020-03-13,,上行趋势,\n")
        with self.assertRaises(CommandError):
            self._run(path)

    def test_an_end_before_the_start_is_a_format_error(self):
        """录反了：这条在比对里一天的并集都不贡献，必须挡在录入之前。"""
        from django.core.management.base import CommandError

        path = self._write("2020-06-30,2020-01-01,高波动,反了\n")
        with self.assertRaises(CommandError):
            self._run(path)

    def test_the_daily_table_still_says_days(self):
        """逐日表的措辞**逐字不变**——那一支不该被区间表这一次改动碰到。"""
        path = self.tmp / "daily.csv"
        path.write_text(
            "日期,档位\n2020-03-12,高波动\n2020-03-13,高波动\n", encoding="utf-8-sig"
        )

        output = self._run(path)
        self.assertIn("原始标注 2 天", output)
        self.assertNotIn("条区间", output)


class TestHighVolIsAnOverlayNotAConflict(SimpleTestCase):
    """高波动是**叠加层**，不是普通档位（CONTEXT.md 第 128 条）。

    真实材料里最常见的形状是「一段下行趋势中某天暴跌」：

        2021-04-14 ~ 2021-07-20  下行趋势
        2021-05-19                高波动      ← 519 暴跌

    这两句不是矛盾，是「这段在跌，其中某天崩了」。把它们判成冲突的代价很具体：
    冲突日退出分母，于是标注越细致，能参与成功标准①的样本反而越少。

    实测一份 44 行材料在旧判据下有 7 条这类「冲突」，占了全部冲突的一半以上。
    """

    def _row(self, line, day, regime, end=None):
        return mod.Row(
            line=line,
            day=date.fromisoformat(day),
            regime=regime,
            note="",
            day_end=date.fromisoformat(end) if end else None,
        )

    def test_a_high_vol_day_inside_a_downtrend_is_not_a_conflict(self):
        """519 暴跌叠在一段下行趋势里——这是本机制的正常形态。"""
        rows = [
            self._row(2, "2021-04-14", BaseRegime.DOWNTREND, "2021-07-20"),
            self._row(3, "2021-05-19", BaseRegime.HIGH_VOL, "2021-05-19"),
        ]
        self.assertEqual(mod.self_conflicts(rows), [])

    def test_two_non_overlay_regimes_still_conflict(self):
        """去掉叠加层之后还剩两个判断在打架 —— 那是**真的**自相矛盾。"""
        rows = [
            self._row(2, "2021-04-14", BaseRegime.DOWNTREND, "2021-07-20"),
            self._row(3, "2021-06-01", BaseRegime.RANGE, "2021-06-30"),
        ]
        self.assertEqual(len(mod.self_conflicts(rows)), 1)

    def test_high_vol_plus_two_real_regimes_is_still_a_conflict(self):
        """折叠掉高波动之后还有两个，仍然报——叠加层不能掩盖真正的矛盾。"""
        rows = [
            self._row(2, "2021-04-14", BaseRegime.DOWNTREND, "2021-07-20"),
            self._row(3, "2021-06-01", BaseRegime.RANGE, "2021-06-30"),
            self._row(4, "2021-06-15", BaseRegime.HIGH_VOL, "2021-06-15"),
        ]
        self.assertEqual(len(mod.self_conflicts(rows)), 1)

    def test_fold_overlay_rules(self):
        """四条折叠规则逐条钉住（`truth.fold_overlay` 是唯一实现）。"""
        fold = truth.fold_overlay
        VOL, DOWN, CALM = BaseRegime.HIGH_VOL, BaseRegime.DOWNTREND, BaseRegime.RANGE

        self.assertEqual(fold({VOL}), {VOL}, "只有高波动时它自己就是结论，不能抹成空集")
        self.assertEqual(fold({VOL, DOWN}), {DOWN}, "叠加层折叠，剩下底层档位")
        self.assertEqual(
            fold({VOL, DOWN, CALM}),
            {VOL, DOWN, CALM},
            "去掉叠加层还有两个在打架 → 原样保留，照报冲突",
        )
        self.assertEqual(fold({DOWN, CALM}), {DOWN, CALM}, "没有叠加层时原样返回")

    def test_the_conflict_definition_is_shared_with_truth(self):
        """命令、`conflicting_days`、`tally` 三处必须是同一个答案。"""
        spans = [
            truth.Interval(
                start=date(2021, 4, 14),
                end=date(2021, 7, 20),
                regime=BaseRegime.DOWNTREND,
            ),
            truth.Interval(
                start=date(2021, 5, 19),
                end=date(2021, 5, 19),
                regime=BaseRegime.HIGH_VOL,
            ),
        ]
        rows = [
            self._row(2, "2021-04-14", BaseRegime.DOWNTREND, "2021-07-20"),
            self._row(3, "2021-05-19", BaseRegime.HIGH_VOL, "2021-05-19"),
        ]

        self.assertEqual(mod.self_conflicts(rows), [])
        self.assertEqual(truth.conflicting_days(spans), set())
        self.assertEqual(truth.tally(spans, {}).conflict_days, 0)
