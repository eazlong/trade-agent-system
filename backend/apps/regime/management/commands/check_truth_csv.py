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
- **`--ai-tidy` 也不是第二条判据**：它只是把原始材料**转写**成 CSV，再交回
  `parse_text`——四类检查因此仍只有一份实现。AI 那一层连词表归一都不做（档位逐字
  照抄、有歧义的日期原样抄），理由见 `apps.regime.truth_tidy` 的模块 docstring。

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
    """CSV 里的一行（已解析）。`line` 是文件里的行号（表头是第 1 行）。

    `day_end` 只在**区间表**上有值：那一行的结束日。逐日表上它恒为 `None`，
    此时这条记录就代表单独一天——于是「逐日」是「区间」在 `day_end is None` 时的
    退化情形，下游的合并、冲突、越界三组检查对两种表说的是同一件事。
    """

    line: int
    day: date
    regime: BaseRegime
    note: str
    day_end: date | None = None

    @property
    def span(self) -> tuple[date, date]:
        """这一行的 `(起, 止)` 闭区间。逐日行的两端是同一天。"""
        return (self.day, self.day_end or self.day)


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
        # detail 自己已经点名行号时不再前置，否则会渲染出「第 3 行：第 3 行（…）」——
        # 两段区间对撞的那条 detail 必须说出**两条**的行号，只靠 `line` 那一列装不下。
        prefix = "" if self.detail.startswith(f"第 {self.line} 行") else f"{where}："
        mark = "" if self.blocking else "（不阻止录入）"
        return f"  [{self.kind}] {prefix}{self.detail}{mark}"


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


def _parse_date(raw: str) -> date | None:
    """CSV 里的日期 → `date`；认不出返回 None。

    **不只认 `YYYY-MM-DD`**：Excel 把 `2020-03-12` 显示成 `2020/3/12` 是常态（它自己的
    默认日期格式），手工敲也一样。只认一种写法会把「格式不对」变成最强的假警报——142 行
    全是同一个问题，人第一反应是「这工具不能用」，而不是「我去改一下日期」。

    只认这四种：带零填充与不带的斜杠、横杠两种。不引 `dateutil` 之类的宽松解析：
    那会把 `3/4/2020` 这种有歧义的写法按某个默认顺序猜掉，而美式 `3/4` 与欧式 `3/4`
    是两个不同的日子——猜错的日子会变成一条假真值，比报错难查得多。
    """
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d ", "%Y/%m/%d "):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
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
    """把 CSV **文件**读成 `(行, 问题)`。**任一问题都意味着这份文件不该被整体录入。**"""
    try:
        # `utf-8-sig`：Excel 导出的 CSV 带 BOM，不剥掉的话第一列表头会变成
        # "\ufeff日期"，于是日期列匹配不上、每一行都报错。
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise CommandError(f"文件不存在：{path}")
    except UnicodeDecodeError as exc:
        raise CommandError(f"文件不是 UTF-8 编码（{exc}）：请另存为 CSV UTF-8")

    return parse_text(text)


#: 区间 CSV 的列名候选。与逐日 CSV 分开两套表头：**「起始日期 + 结束日期」一起出现
#: 就是区间表的判据**，不另加一个 --format 开关去让调用方声明——那种开关的后果是
#: 「声明错了不报错，只是按另一套规则解析出一堆看起来正常的结果」。
_START_HEADERS = ("起始日期", "开始日期", "start", "start_date")
_END_HEADERS = ("结束日期", "end", "end_date")


def _looks_like_intervals(header: list[str]) -> bool:
    """这份表头是区间表还是逐日表。**两条列名都认得出才算区间表**。"""
    return (
        _pick(header, _START_HEADERS) is not None
        and _pick(header, _END_HEADERS) is not None
    )


def parse_intervals_text(text: str) -> tuple[list[Row], list[Problem]]:
    """把**区间 CSV 文本**读成 `(行, 问题)`。

    返回的 `Row` 复用逐日那条路的类型：`day` 装区间起点、`day_end` 装终点。于是合并、
    自相矛盾、越界三组检查全部照旧复用——它们问的问题（哪几天被标成了什么）在两种表上
    完全一样。

    **一条区间少了一端就报错**，不补成「当天」：宽松地补一个「当天」会让一条本该覆盖
    半年的区间悄悄缩成一天。那正是 v1 那个 bug 的形状，只是换成静默发生——真实材料跑
    出来时约 2450 天只覆盖了 30 天。
    """
    rows: list[Row] = []
    problems: list[Problem] = []

    reader = csv.reader(text.splitlines())
    try:
        header = next(reader)
    except StopIteration:
        raise CommandError("文件是空的")

    start_col = _pick(header, _START_HEADERS)
    end_col = _pick(header, _END_HEADERS)
    regime_col = _pick(header, _REGIME_HEADERS)
    note_col = _pick(header, _NOTE_HEADERS)

    missing = []
    if start_col is None:
        missing.append("起始日期")
    if end_col is None:
        missing.append("结束日期")
    if regime_col is None:
        missing.append("档位")
    if missing:
        problems.append(Problem(None, "表头", "找不到这些列：" + "；".join(missing)))
        return rows, problems

    for offset, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue

        need = max(start_col, end_col, regime_col) + 1
        if len(record) < need:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"列数不足（需要至少 {need} 列，实际 {len(record)} 列）",
                )
            )
            continue

        raw_start = record[start_col].strip()
        raw_end = record[end_col].strip()
        raw_regime = record[regime_col].strip()

        # 档位留空 = 原文没写档位。与逐日那条路同一条语义：这不是错误。
        if not raw_regime:
            continue

        start = _parse_date(raw_start)
        if start is None:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"起始日期解析不了：{raw_start!r}"
                    f"（这一条填了档位 {raw_regime!r}；认得 2020-03-12 / 2020/3/12）",
                )
            )
            continue

        end = _parse_date(raw_end)
        if end is None:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"结束日期解析不了：{raw_end!r}"
                    "——区间必须两端都给，不能把一条长区间当成一天",
                )
            )
            continue

        regime = _parse_regime(raw_regime)
        if regime is None:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"{start} 的档位认不出：{raw_regime!r}"
                    "（只认 高波动/下行趋势/上行趋势/箱体震荡 或 high_vol/downtrend/uptrend/range）",
                )
            )
            continue

        if end < start:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"区间终点 {end} 早于起点 {start}——录反了，这条在比对里一天的并集都不贡献",
                )
            )
            continue

        note = (
            record[note_col].strip()
            if note_col is not None and note_col < len(record)
            else ""
        )
        rows.append(Row(line=offset, day=start, regime=regime, note=note, day_end=end))

    return rows, problems


def parse_text(text: str) -> tuple[list[Row], list[Problem]]:
    """按表头自动分派：区间表走 `parse_intervals_text`，逐日表走逐日解析。

    **分派只在读表头那一刻做一次**：两种表列名不同，一份表要么是这一种、要么是那一种，
    不存在「两种都试一遍取更好的结果」——那会让一份坏掉的表以另一种形状「成功」解析
    出来，而它的每一行都看着正常。

    这是 AI 整理层与命令行共用的唯一入口：`truth_tidy` 产出的是区间表，人工手写的裸模板
    是逐日表，两者都从这里进，四类检查因此只有一份实现。
    """
    reader = csv.reader(text.splitlines())
    try:
        header = next(reader)
    except StopIteration:
        raise CommandError("文件是空的")

    if _looks_like_intervals(header):
        return parse_intervals_text(text)
    return _parse_daily_text(text)


def _parse_daily_text(text: str) -> tuple[list[Row], list[Problem]]:
    """把**逐日** CSV 文本读成 `(行, 问题)`——`parse` 去掉读盘之后的那一半。

    **拆出来的唯一理由是 AI 整理那条路要复用这四类检查**（见 `truth_tidy`）：那一层
    产出的是一段 CSV 文本，还没落盘；让它先写文件再读回来，等于凭空多一次 IO 和一个
    「写哪儿、谁清理」的问题，而检查逻辑还是同一份。

    所以这里**不是**一次重构：`parse` 的行为逐位不变（它现在只是读盘 + 委托），
    而「四类问题的判据只有一份」这件事从此由**签名**保证——两条路都只能走到这里。
    """
    rows: list[Row] = []
    problems: list[Problem] = []

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

        day = _parse_date(raw_date)
        if day is None:
            problems.append(
                Problem(
                    offset,
                    "格式",
                    f"日期解析不了：{raw_date!r}"
                    f"（这一行填了档位 {raw_regime!r}；认得 2020-03-12 / 2020/3/12）",
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

    ordered = sorted(rows, key=lambda r: r.span)
    spans: list[truth.Interval] = []
    first = ordered[0]
    current = truth.Interval(
        start=first.span[0],
        end=first.span[1],
        regime=first.regime,
        note=first.note,
    )
    for row in ordered[1:]:
        start, end = row.span
        # 相邻 = 下一条的起点正好接在当前这条终点后一天。**区间行**的终点取自它自己
        # （`day_end`），逐日行的终点就是它那一天——两种表在这里走的是同一句话。
        adjacent = (start - current.end).days == 1
        if adjacent and row.regime is current.regime:
            current = truth.Interval(
                start=current.start,
                end=end,
                regime=current.regime,
                note=current.note,
            )
        else:
            spans.append(current)
            current = truth.Interval(
                start=start, end=end, regime=row.regime, note=row.note
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
    # **按「区间对」报，不按天报。** 两条各跨半年的区间互相对撞时，按天展开会产生
    # 上百条同一形状的问题，而它们要人做的是**同一个动作**（改其中一条）。实测一份
    # 44 行的真实材料刷出 435 条，那种输出没人会读——而「没人读」正是这个命令最怕的
    # 失效方式。
    #
    # 判据仍是 `truth.conflicting_days` 那一句（按天展开、看档位集合大小），这里只是
    # 把「哪些天冲突」收成「哪几行对撞」。同行不算（一条区间自己与自己重不了）。
    out: list[Problem] = []
    ordered = sorted(rows, key=lambda r: r.span)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            lo = max(left.span[0], right.span[0])
            hi = min(left.span[1], right.span[1])
            if lo > hi:
                continue  # 不相交
            if left.regime is right.regime:
                continue  # 同档位重叠：互相佐证，不是冲突
            # **叠加层不参与互斥**：`{高波动, 下行趋势}` 是「这段在跌，其中某天崩了」，
            # 不是矛盾。判据取自 `truth.fold_overlay`——那是这份规则唯一的一处实现，
            # 与 `tally` / `conflicting_days` 同源；在这里另抄一份「高波动不算冲突」
            # 会让录入时的报数与本命令报的数分家。
            if len(truth.fold_overlay({left.regime, right.regime})) <= 1:
                continue
            days = (hi - lo).days + 1
            out.append(
                Problem(
                    left.line,
                    "自相矛盾",
                    f"第 {left.line} 行（{left.span[0]}~{left.span[1]} "
                    f"{left.regime.display}）与第 {right.line} 行"
                    f"（{right.span[0]}~{right.span[1]} {right.regime.display}）"
                    f"重叠 {days} 天（{lo} → {hi}）——这些天会成为冲突日、退出分母"
                    "（等于白标），改其中一条即可",
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
        parser.add_argument(
            "--ai-tidy",
            action="store_true",
            help=(
                "把 path 当成**原始材料**（一段话/一张乱格式的表），先交给 AI 转写成 CSV "
                "再走同一条检查。AI 只转写，不判断档位、不填空白、不写库"
            ),
        )
        parser.add_argument(
            "--out",
            default=None,
            metavar="CSV",
            help=(
                "把 AI 转写出来的 CSV 落在这里（只在 --ai-tidy 时有意义）。"
                "不给就只打印到 stdout，不写任何文件"
            ),
        )

    def handle(self, *args, **options):
        path = Path(options["path"]).expanduser()

        source = path
        if options["ai_tidy"]:
            rows, problems, source = self._ai_tidy(path, options["out"])
        else:
            rows, problems = parse(path)

        self.stdout.write(f"文件：{source}")
        self.stdout.write(f"解析到 {len(rows)} 行已标注日")

        problems.extend(self_conflicts(rows))

        spans = merge(rows)
        if rows:
            span_days = sum(s.days for s in spans)
            # 一行是什么，**按表决定**：区间表的每一行本来就是一段，逐日表的每一行是一天。
            # 混成一句「原始标注 N 天」会在区间表上说出「32 天」——而那 32 行覆盖的是
            # 4251 天，读的人会以为标得很稀。
            unit = "条区间" if self._is_interval_table(rows) else "天"
            self.stdout.write(
                f"合并为 {len(spans)} 段区间（覆盖 {span_days} 天；"
                f"原始标注 {len(rows)} {unit} → 相邻同档位已合并）"
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

    @staticmethod
    def _is_interval_table(rows: list[Row]) -> bool:
        """这些行是区间表还是逐日表。

        判据是「有没有任何一行带终点」：区间表的每一行都有 `day_end`，逐日表的每一行
        都是 `None`。**不重新读表头**——表头在解析那一刻已经用过一次，再用一次就是
        同一件事的第二处判断，而两处迟早分家。
        """
        return any(row.day_end is not None for row in rows)

    # -- AI 整理 ----------------------------------------------------------- #

    def _ai_tidy(
        self, path: Path, out: str | None
    ) -> tuple[list[Row], list[Problem], Path | str]:
        """先把原始材料交给 AI 转写成 CSV，再走**同一条**检查链。

        返回 `(行, 问题, 显示用的来源)`。来源是「哪里来的这份 CSV」：落盘了就是那个
        文件，没落盘就是一句「AI 转写结果（未落盘）」——报告的第一行必须说清人手里这份
        表是从哪儿冒出来的。

        ## 失败就地收场，不降级成「没有标注」

        整理失败（两跳全挂 / 答复不合形状）时抛 `CommandError`，与「格式错」同一档。
        **不能**退化成「0 行已标注日 + 检查通过」：那正是这个命令存在的意义所在——
        一份整理失败的材料看起来与一份没填档位的模板一模一样。

        ## AI 那一层的问题与 CSV 那一层的问题**不混**

        转写问题（没有 source_text、档位不在原文里）在 `truth_tidy` 里已经折成
        `status="failed"`，到不了这里；这里出现的问题永远是 CSV 层的四类，因为
        `parse_text` 是唯一入口。两层的词表因此不会互相污染。
        """
        from apps.regime import truth_tidy

        try:
            raw = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            raise CommandError(f"文件不存在：{path}")
        except UnicodeDecodeError as exc:
            raise CommandError(f"文件不是 UTF-8 编码（{exc}）：请另存为 UTF-8")

        try:
            outcome = truth_tidy.tidy_raw(raw)
        except truth.TruthInputError as exc:
            raise CommandError(str(exc))

        if not outcome.ok:
            raise CommandError(f"AI 整理失败：{outcome.error}")

        self.stdout.write(
            self.style.WARNING(
                "AI 只做了转写（档位逐字照抄、空白不填）——"
                "档位判断仍由人负责，下面的检查不含任何算法结论。"
            )
        )

        if out:
            target = Path(out).expanduser()
            # `utf-8-sig`：与模板一致（Excel 会带 BOM），回读时 `parse` 也剥它。
            target.write_text(outcome.csv, encoding="utf-8-sig")
            self.stdout.write(f"已把转写结果写到：{target}")
            return (*parse(target), target)

        self.stdout.write("")
        self.stdout.write("转写结果（未落盘；要留档请加 --out <文件名>）：")
        for line in outcome.csv.rstrip().splitlines():
            self.stdout.write(f"  {line}")
        self.stdout.write("")

        rows, problems = parse_text(outcome.csv)
        return rows, problems, "AI 转写结果（未落盘）"

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

        # 区间行的越界看**两端**：一条跨过日线起点的区间，前半段算法够不着。
        # 只看起点会把整条划成「在里面」，而它实际有一半进了 missing。
        before = [r for r in rows if r.span[1] < first]
        after = [r for r in rows if r.span[0] > last]
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
