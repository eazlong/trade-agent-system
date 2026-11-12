"""校验人工标注 CSV——**在录入之前**，不写库。

## 为什么需要这个命令

`/regime label add` 与 `truth_run.add` 一次只吃一段区间，而标注是一张表（几十上百行）。
逐行敲进去的代价不是麻烦，是**错一处就得撤回一次**：`RegimeTruthInterval` 是比对的基准，
撤回走软删、不删行（理由见模型 docstring），于是「录错了再撤」会在库里留下痕迹。
更糟的是「录对了但语义错了」——比如两段档位不同的区间在边界上重叠了一天，那一天就成了
冲突日、悄悄退出分母，而录入时的回复只报一个「新增 N 个冲突日」，人未必会追。

所以这道闸放在**写之前**：先把 CSV 摊开，把「格式错」「档位名认不出」「日期越界」
「自己跟自己打架」四类问题一次说清，再让人决定要不要录。

## 它不做什么

- **不写库**：本命令只读。要录是 `--apply` 的另一条路（不在这一版里，见下）。
- **不算一致率**：那会把算法输出递到标注者眼前，而第 164 条要求「标注必须在算法输出
  可见之前完成」。这道闸只回答「这份 CSV 能不能被录入」，一个字都不说「标得对不对」。
  唯一的例外是 `--show-coverage`：它只说「这段落在日线范围里/外」，那是数据可得性，
  不是算法结论。
- **不替人猜**：空档位就是空，不当成「沿用上一行」。

## CSV 形状

两种都认，列名按表头匹配（顺序无关、多余列忽略）：

    最简：     日期,档位
    带备注：   日期,收盘价USDT,前一日涨跌%,档位（待填）,备注（待填）

`备注` 列名里带不带「（待填）」都行。`档位` 收中文名（高波动 / 下行趋势 / 上行趋势 /
箱体震荡）与 slug（high_vol / downtrend / uptrend / range），与 `/regime label add`
同一套翻译（`regime_commands._regime_of` 的语义，这里不重复实现，直接比枚举）。

## 一天一行 → 区间怎么合

标注表是逐日的，而 `RegimeTruthInterval` 是**区间**。合并规则只有一条：**相邻且档位相同
的日子合成一段**。这不需要任何判断，也不改变语义——`truth.day_regimes` 按日展开，
一段 5 天与五段 1 天的覆盖集合完全相同。

**但合并会藏住一个事实**：逐日标注者未必想表达「这 5 天是一个连续的阶段」。所以在报告里
把「合并后段数」与「原始标注天数」并排列出，让人看得见这一步做了什么。

## 报出来的四类问题

1. **格式**：日期解析不了、档位认不出、行数与列数对不上。**有一行错就整体不可录**——
   跳过坏行继续录会得到一份「看起来完整、实际缺了几段」的真值，而缺的那几段会让
   一致率的分母悄悄变小。
2. **越界**：日期早于日线起点、或晚于最后一根已收盘日线。越界**不是错**（第 164 条：
   落在算法够不着的时期里的天会全部进 `missing`），但必须报出来——否则「标了 200 天，
   其中 180 天算法没输出」这件事要等到算一致率才被发现。
3. **自相矛盾**：同一份 CSV 里同一天出现两个不同档位（通常是两份表拼在一起、或边界
   写重了）。这些天会成为冲突日、退出分母，**等于白标**。
4. **与库里已有区间打架**：`--against-db` 时额外查一次。它不是必须的（第一次录入时
   库是空的），但重复导入同一份表时能立刻发现。

退出码：有问题返回 1，全清白返回 0。**0 个问题不等于「可以录」**——它只表示这份 CSV
在格式与自洽性上过得去。
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError

from apps.regime import truth
from apps.regime.quant import BaseRegime

#: 档位列可能叫的名字。按顺序取第一个命中的。
_REGIME_HEADERS = ("档位（待填）", "档位", "regime", "人工标注", "标注")

#: 备注列可能叫的名字。
_NOTE_HEADERS = ("备注（待填）", "备注", "note")

#: 日期列可能叫的名字。
_DATE_HEADERS = ("日期", "date", "起始日期", "start")


@dataclass(frozen=True)
class Row:
    """CSV 里的一行（已解析）。`line` 是文件里的行号（表头是第 1 行）。"""

    line: int
    day: date
    regime: BaseRegime
    note: str


@dataclass(frozen=True)
class Problem:
    """一条问题。`line` 为 None 时是整份文件层面的问题（表头缺失之类）。

    `blocking` 决定它挡不挡录入。**越界是唯一不挡的那一类**：CONTEXT.md 第 164 条明写
    落在算法够不着的时期里的天会全部进 `missing`（按不匹配计、单列），那是如实计数，
    不是错。把它做成硬错误会逼人删掉自己确信的标注——而那些标注恰好是最有价值的那部分
    （「我知道那几天是什么，算法还没资格说话」）。

    其余三类（格式错、自相矛盾、与库冲突）都挡：它们要么让整份文件缺段，要么让标的那天
    白标，两种都是「看起来录进去了、实际没算数」。
    """

    line: int | None
    kind: str
    detail: str
    blocking: bool = True

    def render(self) -> str:
        where = f"第 {self.line} 行" if self.line else "整份文件"
        mark = "" if self.blocking else "（不阻止录入）"
        return f"  [{self.kind}] {where}：{self.detail}{mark}"


def _pick(header: list[str], candidates: Iterable[str]) -> int | None:
    """表头里第一个匹配的列下标；一个都没有返回 None。

    去空白再比：Excel 另存出来的表头常带尾随空格，而那会让「档位」匹配不上，
    表现是「每一行都说找不到档位列」——一个看起来像内容错、其实是表头没对上的报错。
    """
    stripped = [h.strip().lstrip("\ufeff") for h in header]
    for name in candidates:
        if name in stripped:
            return stripped.index(name)
    return None


def _parse_regime(raw: str) -> BaseRegime | None:
    """中文名或 slug → 枚举。认不出返回 None（由调用方记成一条问题）。"""
    text = (raw or "").strip()
    if not text:
        return None
    for member in BaseRegime:
        if text == member.display or text == member.value:
            return member
    return None


def parse(path: Path) -> tuple[list[Row], list[Problem]]:
    """把 CSV 读成 `(行, 问题)`。**任一问题都意味着这份文件不该被整体录入。**"""
    rows: list[Row] = []
    problems: list[Problem] = []

    try:
        # `utf-8-sig`：Excel 导出的 CSV 带 BOM，不剥掉的话第一列表头会变成
        # "\ufeff日期"，于是日期列匹配不上、每一行都报错。
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise CommandError(f"文件不存在：{path}")
    except UnicodeDecodeError as exc:
        raise CommandError(f"文件不是 UTF-8 编码（{exc}）：请另存为 CSV UTF-8")

    reader = csv.reader(text.splitlines())
    try:
        header = next(reader)
    except StopIteration:
        raise CommandError("文件是空的")

    date_col = _pick(header, _DATE_HEADERS)
    regime_col = _pick(header, _REGIME_HEADERS)
    note_col = _pick(header, _NOTE_HEADERS)

    missing = []
    if date_col is None:
        missing.append(f"日期（认得的列名：{'/'.join(_DATE_HEADERS)}）")
    if regime_col is None:
        missing.append(f"档位（认得的列名：{'/'.join(_REGIME_HEADERS)}）")
    if missing:
        problems.append(Problem(None, "表头", "找不到这些列：" + "；".join(missing)))
        return rows, problems

    for offset, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue  # 全空行：Excel 常在末尾留几行，不当成错误

        need = max(date_col, regime_col) + 1
        if len(record) < need:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"列数不足（需要至少 {need} 列，实际 {len(record)} 列）",
                )
            )
            continue

        raw_date = record[date_col].strip()
        raw_regime = record[regime_col].strip()

        # **档位留空 = 这一行没标**，不是错误。
        #
        # 模板刻意预填了全部日期（只给价格），人只在自己确信的日子填档位。于是「日期有、
        # 档位空」是模板的**正常状态**——把它报成错会在空白模板上刷出几百条假错误，而假
        # 错误会训练人整个跳过这个命令，它存在的意义恰恰是让人肯看它。
        #
        # 反过来的「档位有、日期空」才是错：不知道该落到哪一天，下面 `strptime` 会报出来。
        if not raw_regime:
            continue

        try:
            day = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"日期解析不了，期望 YYYY-MM-DD：{raw_date!r}"
                    f"（这一行填了档位 {raw_regime!r}）",
                )
            )
            continue

        regime = _parse_regime(raw_regime)
        if regime is None:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"{day} 的档位认不出：{raw_regime!r}"
                    "（只认 高波动/下行趋势/上行趋势/箱体震荡 或 high_vol/downtrend/uptrend/range）",
                )
            )
            continue

        note = (
            record[note_col].strip()
            if note_col is not None and note_col < len(record)
            else ""
        )
        rows.append(Row(line=offset, day=day, regime=regime, note=note))

    return rows, problems


def merge(rows: list[Row]) -> list[truth.Interval]:
    """逐日标注 → 区间：**相邻且档位相同**的日子合成一段。

    `note` 取这一段里第一天的（合并之后一天一个备注没有意义，而丢掉全部备注又会让
    「这段是在标什么事」在库里查不到——取第一个是折中，且它通常是那个事件的起点）。
    """
    if not rows:
        return []

    ordered = sorted(rows, key=lambda r: r.day)
    spans: list[truth.Interval] = []
    current = truth.Interval(
        start=ordered[0].day,
        end=ordered[0].day,
        regime=ordered[0].regime,
        note=ordered[0].note,
    )
    for row in ordered[1:]:
        adjacent = (row.day - current.end).days == 1
        if adjacent and row.regime is current.regime:
            current = truth.Interval(
                start=current.start,
                end=row.day,
                regime=current.regime,
                note=current.note,
            )
        else:
            spans.append(current)
            current = truth.Interval(
                start=row.day, end=row.day, regime=row.regime, note=row.note
            )
    spans.append(current)
    return spans


def self_conflicts(rows: list[Row]) -> list[Problem]:
    """同一份 CSV 里同一天出现多个不同档位。**回报到具体行号。**

    判据与 `truth.conflicting_days` 是同一句话（按日展开、看档位集合的大小），但不复用它
    而是自己展开一遍：那个函数只回答「哪些天冲突」，而这里还要回答「是哪几行造成的」——
    行号是这份报告唯一能让人动手改的东西，而「2020-03-12 冲突」这种话在几百行的表里
    找不到落点。

    判断口径仍与它一致（同档位重叠**不算**冲突，那是互相佐证），由下面 `len(set) > 1`
    钉住；`test_truth.py` 里 `conflicting_days` 的用例覆盖了同一条语义。
    """
    by_day: dict[date, list[Row]] = defaultdict(list)
    for row in rows:
        by_day[row.day].append(row)

    out: list[Problem] = []
    for day in sorted(by_day):
        bucket = by_day[day]
        distinct = {row.regime for row in bucket}
        if len(distinct) < 2:
            continue
        detail = "；".join(f"第 {row.line} 行标 {row.regime.display}" for row in bucket)
        out.append(
            Problem(
                bucket[0].line,
                "自相矛盾",
                f"{day} 被标了 {len(distinct)} 个不同档位（{detail}）"
                "——这一天会成为冲突日、退出分母（等于白标）",
            )
        )
    return out


class Command(BaseCommand):
    help = "校验人工标注 CSV（只读，不写库）：格式 / 越界 / 自相矛盾 / 与库冲突"

    def add_arguments(self, parser):
        parser.add_argument("path", help="CSV 文件路径")
        parser.add_argument(
            "--against-db",
            action="store_true",
            help="额外检查与库里已有活区间的冲突（第一次录入时不需要）",
        )
        parser.add_argument(
            "--show-coverage",
            action="store_true",
            help="报出标注覆盖的天数落在日线范围里/外各多少（只说数据可得性，不含算法结论）",
        )

    def handle(self, *args, **options):
        path = Path(options["path"]).expanduser()
        rows, problems = parse(path)

        self.stdout.write(f"文件：{path}")
        self.stdout.write(f"解析到 {len(rows)} 行已标注日")

        problems.extend(self_conflicts(rows))

        spans = merge(rows)
        if rows:
            span_days = sum(s.days for s in spans)
            self.stdout.write(
                f"合并为 {len(spans)} 段区间（覆盖 {span_days} 天；"
                f"原始标注 {len(rows)} 天 → 相邻同档位已合并）"
            )

        if options["show_coverage"]:
            problems.extend(self._coverage(rows))

        if options["against_db"]:
            problems.extend(self._against_db(spans))

        blocking = [p for p in problems if p.blocking]
        advisory = [p for p in problems if not p.blocking]

        if problems:
            self.stdout.write("")
            if blocking:
                self.stdout.write(
                    self.style.ERROR(f"发现 {len(blocking)} 个阻止录入的问题：")
                )
                for problem in blocking:
                    self.stdout.write(problem.render())
            if advisory:
                self.stdout.write(
                    self.style.WARNING(f"另有 {len(advisory)} 条提示（不阻止录入）：")
                )
                for problem in advisory:
                    self.stdout.write(problem.render())
            if blocking:
                raise CommandError(f"{len(blocking)} 个问题")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("格式与自洽性检查通过。"))
        if spans:
            self.stdout.write("")
            self.stdout.write("将录入以下区间（相邻同档位已合并）：")
            for span in spans:
                note = f"  {span.note}" if span.note else ""
                self.stdout.write(
                    f"  {span.start} → {span.end}  {span.regime.display}  ({span.days} 天){note}"
                )
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "注意：本命令不含任何算法结论，也不判断标得对不对。"
                "「0 个问题」只表示格式与自洽性过得去。"
            )
        )

    # -- 两类附加检查 ------------------------------------------------------- #

    def _coverage(self, rows: list[Row]) -> list[Problem]:
        """标注日落在日线范围里/外各多少。**只说数据可得性，不说判得对不对。**

        第 164 条允许标到算法够不着的时期（那些天会全部进 `missing`，按不匹配计但单列），
        所以越界不是错。报出来是因为「标了 200 天、其中 180 天算法没输出」这件事，
        等到算一致率才发现就太晚了。
        """
        from apps.regime.models import DailyCandle

        from apps.regime import config

        bounds = (
            DailyCandle.objects.filter(symbol=config.CANDLES.symbol)
            .order_by("date")
            .values_list("date", flat=True)
        )
        first = bounds.first()
        last = bounds.last()
        if first is None:
            return [Problem(None, "覆盖", "日线表是空的，无法判断越界——先回填日线")]

        before = [r for r in rows if r.day < first]
        after = [r for r in rows if r.day > last]
        inside = len(rows) - len(before) - len(after)

        self.stdout.write("")
        self.stdout.write(f"日线范围：{first} → {last}")
        self.stdout.write(
            f"  范围里 {inside} 天；早于起点 {len(before)} 天；晚于终点 {len(after)} 天"
        )

        out: list[Problem] = []
        if before:
            days = sorted(r.day for r in before)
            out.append(
                Problem(
                    None,
                    "覆盖",
                    f"{len(before)} 天早于日线起点 {first}（{days[0]} → {days[-1]}）："
                    "这些天算法没有输出，会全部进 missing（按不匹配计，单列）——"
                    "第 164 条允许标到算法够不着的时期",
                    blocking=False,
                )
            )
        if after:
            days = sorted(r.day for r in after)
            out.append(
                Problem(
                    None,
                    "覆盖",
                    f"{len(after)} 天晚于最后一根已收盘日线 {last}（{days[0]} → {days[-1]}）："
                    "这些天还没有日线，等日线补齐后会被正常比对",
                    blocking=False,
                )
            )
        return out

    def _against_db(self, spans: list[truth.Interval]) -> list[Problem]:
        """与库里已有**活**区间是否打架。同档位重叠是佐证，不同档位才是冲突。"""
        from apps.regime import truth_run

        existing = truth_run.live_intervals()
        if not existing:
            self.stdout.write("")
            self.stdout.write("库里还没有任何活区间——这是第一次录入。")
            return []

        bad: list[Problem] = []
        for span in spans:
            for old in existing:
                if max(span.start, old.start) > min(span.end, old.end):
                    continue  # 不重叠
                if old.regime is span.regime:
                    continue  # 同档位重叠：互相佐证，不是问题
                lo = max(span.start, old.start)
                hi = min(span.end, old.end)
                bad.append(
                    Problem(
                        None,
                        "库冲突",
                        f"{lo} → {hi} 这份标 {span.regime.display}，"
                        f"库里 #{old.id} 标 {old.regime.display}（重叠 {(hi - lo).days + 1} 天）",
                    )
                )
        if not bad:
            self.stdout.write("")
            self.stdout.write(f"与库里 {len(existing)} 段活区间无档位冲突。")
        return bad
