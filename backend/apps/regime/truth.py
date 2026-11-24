"""人工真值与成功标准①（CONTEXT.md 第 163、164 条）：纯函数层。

`truth_run.py` 是它的另一半（从库里取区间、把区间写回库）。本模块只吃「一串区间 + 一张
『日期 → 阶段』表」，不吃 DB、不读时钟、不认 Shadow 记录。

## 比对的是谁

比对的两边是：

| 边 | 是什么 | 从哪来 |
|----|--------|--------|
| 真值 | 人工标注的**已知**历史区间 | `RegimeTruthInterval`，人敲进去的 |
| 判定 | **纯量化口径**的历史阶段标签 | `slice.build_tags`（= `slicing.load_tags`） |

右边刻意取**纯量化口径**而不是 `RegimeJudgement` 表里的历史行（CONTEXT.md 第 77 条）：
第 77 条把一致率的比对基准钉在「纯量化口径」上，而 `judgement` 行是**当日生效**的结论，
它带着资讯抬升（第 162 条的第二个值）——拿它当分母，等于问「机制当时听没听懂新闻」，
而第 163 条问的是「量化判定准不准」。

**但抬升的那几天要单独摘出去**（ADR 0002 / 第 77 条的必然推论）：拿纯量化标签去比，在
「当天在生效的判定被抬升过」的日子上是不公平的——机制那天的答案本来就不是量化给的。
最常见的形状是极端行情：人工标〈高波动〉，纯量化标签还是〈箱体震荡〉，于是**机制靠资讯
判对了的那一天反而把①判成不达标**。那些天因此**不进分母**，单独报一个数。

判据是 `in_force` 里那天 `(基础阶段, 生效阶段)` 不相等；「哪天在生效的是哪条」由
`judgement.in_force_days` 按**生效轴**给出（`effective_at <= 业务日界(那天)` 的最新一条），
**不是**按签署日：抬升要次日 08:00 才生效，按签署日归会差两天，而那种错位处处自洽。

判据刻意**不是**「抬升标志非空」：基础阶段已经是高波动时，资讯照抬升、标志照记，但生效
阶段与基础阶段相同——那天机制给出的**就是**纯量化答案，排除它等于把最极端的日子从分母里
挑走，一致率只会被抬上去。

用 `build_tags` 而不是另写一份「历史标签」的取数，是因为 `quant.label_series` 是**实时
判定与历史回溯的同一份实现**：两处各写一遍，一致率比的就成了两个算法之间的差异，而不是
算法与人的差异。代价是这里必须跟着标签的「不落库、每次重算」约定走——不许在本模块里
缓存标签（`slicing.load_tags` 的 docstring 说了缓存会把保证换成「与缓存建立那一刻的算法
一致」，而那是静默的）。

## 几个数不是一回事

一段区间喂进来，天数会分成五摊（另有 `days` 是它们的和）：

- `counted`：**分母**。区间覆盖、当天人工档位唯一确定、那天不是箱体区间、且那天机制用的
  就是纯量化结论。
- `boxed`：人工标成〈箱体震荡〉的**趋势外**日子。**不进分母**（ADR 0007）：箱体不可机械
  识别（`detect_box_range` 在 2518 天里零检出），把它算进分母会让一致率变成一个说不出
  是「哪一类日子的一致率」的数。它**单列**而不只是被扣掉，因为「75.0% 是除箱体外的
  一致率」与「75.0% 是全档位的一致率」是两句完全不同的话——不印这个数，口径修订就只
  活在 ADR 里。

  **分母里剩的不只是趋势区间。** 人工**单独**标〈高波动〉的日子（`{高波动}`）按
  `fold_overlay` 是保留的、不折叠，因此它不进 `boxed`、照常进分母。所以口径句一律
  按「除箱体外都计入」正面说，不枚举「上行 / 下行计入」——后者在一份含高波动日的标注上
  会变成假话，而它恰恰是用来防误读的那一句。
- `missing`：算法当天**没有输出**（日线缺、预热不足）。按**不匹配**计（进分母、不进分子），
  但**单列**——「判错了」与「没判」是两种毛病，混成一个数会让「日线没回填」看起来像
  「量化判反了」。
- `conflict_days`：被**两段不同档位**的人工区间同时覆盖的日子。**不进分母**——人工真值
  自己打架的那天，算法无论答什么都是错的，把它算进分母等于让「标注矛盾」去惩罚算法。
- `escalated`：当天**在生效**的判定带资讯抬升（见上）。**不进分母**。
  `escalated_matched` 是其中「人工标注与当天的生效阶段一致」的天数——它只是把这次排除
  做得**可审计**（否则那几天就是一个看不见底的黑洞），**不参与达标判断**：①是量化判定
  的准入门槛，资讯层判得对不对不该决定①达标与否。
- `days`：所有活区间覆盖的自然日**并集**（不是各段之和）。同一天被两段**同档位**覆盖只
  算一天，且两段互为佐证。

**五摊互斥，顺序是「冲突 → 抬升 → 箱体 → 缺口 → 比对」**（`tally` 里就是一个 `continue`
链），所以 `days == conflict_days + escalated + boxed + counted` 永远成立。

箱体排在抬升**之后**：抬升日说的是「那天机制用的不是纯量化结论」，它与人工标了什么无关，
是更硬的一层「不能比」；排在冲突**之后**同理（真值自己没定义的那天，理由更强）。

**箱体退出的是分母，不是警报。** 「系统性错向」这条判据原样保留——它的语义是「把要命的
行情判成平静」，本来就不需要箱体召回才有效。箱体那几天的人工标注是〈箱体震荡〉，永远不
可能触发「人工标极端、算法判箱体」，所以这两件事在实现上也不相交（见 `EXTREME` / `CALM`）。

## 系统性错向：单向，一次即不达标（第 164 条）

「把极端行情判成箱体震荡」**哪怕一次**也算不达标。这条是单向的：

- 人工标〈高波动〉或〈下行趋势〉，算法判〈箱体震荡〉⇒ **错向，计一次**。
- 反向（人工标〈箱体震荡〉，算法判〈高波动〉）⇒ 只算普通的不一致。

单向不是漏了一半，而是这一半正是要堵的路。箱体震荡是**兜底档**（`quant.PRIORITY`
的末位）——「既非高波动、也无趋势」的剩余，所以一个退化成「永远答箱体震荡」的机制能
拿到不低的一致率，而它恰恰在**该保命的日子里说不必保命**。反过来的错（把箱体判成高波动）
是保守方向，代价是多停几次策略，不是爆仓。

一致的阈值（`config.SHADOW.min_agreement_rate`）与这一条**同时**要满足（第 163 条：
两条一起才拦得住退化机制）。

## 这一版没做的

- **不逐日标注**：第 164 条要的是「已知区间」，能进真值的只有「我确定那段时间是熊市」
  这一种东西。逐日打标会让「有争议的日子」也混进来，而一个错的真值会把判对的日子记成
  判错。表里也就没有置信度、依据、来源这些字段。
- **不加锁**：录入与比对不互斥。一个正在被读的区间被撤回，最坏结果是这一次的一致率里
  多/少了一段，下一次重算就对了——比对上锁会换来一个「读到一半的清单」这种更坏的东西。
- **不判断区间是否落在有日线的范围里**：起点早于日线起点的区间，那些天会全部落进
  `missing`（算法确实没输出）。这是如实计数，不是 bug；`describe` 会把 `missing` 单列
  出来，让「录了一段算法够不着的时期」能被看见。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping

from apps.regime import config
from apps.regime.quant import BaseRegime

#: 极端档。人工标了这两档而算法判〈箱体震荡〉= 系统性错向（第 164 条）。
#:
#: 用 `frozenset` 而不是「`PRIORITY` 的前两名」：这里的成员是**语义**（会要命的行情），
#: 不是排序位置。哪天 `PRIORITY` 插进第五档，按位置取会把新档位悄悄算成极端档。
EXTREME: frozenset[BaseRegime] = frozenset({BaseRegime.HIGH_VOL, BaseRegime.DOWNTREND})

#: 兜底档。错向看的是「把极端判成了它」。
CALM = BaseRegime.RANGE


class TruthInputError(Exception):
    """人工真值入口共用的「这件事办不成」。

    **只说事实，不说怎么改。** 它同时服务管理命令与 Telegram 的 `/regime label`：
    「改哪个参数」只有命令行知道，「换一条命令怎么写」只有 slash 入口知道。与
    `deactivation_run.ExemptionError` 同一条理由。
    """


def each_day(start: date, end: date) -> Iterable[date]:
    """闭区间 `[start, end]` 里的每一天。**含两端**（区间是闭的，两处口径必须一致）。"""
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


@dataclass(frozen=True)
class Interval:
    """一段人工标注：`[start, end]` 这些天是 `regime`。

    `id` 只在库里那一份上有值（新建的区间还没有主键）。它是 `None` 时 `tally` 照算——
    比对不需要主键，只有「报冲突时说清是哪两段」才需要。

    `note` 是给人看的一句话（「某轮牛市」），不参与任何计算，也**不许**拿它当判断依据：
    第 164 条不许引用第三方划分，所以这里没有「来源」字段可填。
    """

    start: date
    end: date
    regime: BaseRegime
    note: str = ""
    id: int | None = None

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise TruthInputError(f"区间终点（{self.end}）早于起点（{self.start}）")
        if not isinstance(self.regime, BaseRegime):
            raise TruthInputError(f"档位不是已知的四档之一：{self.regime!r}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __str__(self) -> str:
        tag = f"#{self.id} " if self.id is not None else ""
        note = f"（{self.note}）" if self.note else ""
        return f"{tag}{self.start}~{self.end} {self.regime.display}{note}"


@dataclass(frozen=True)
class Agreement:
    """一次比对的结果。**没有「分数」这种东西**——四个计数各自成立，`rate` 只是一个商。

    `intervals` / `days` 是输入侧的事实（录了几段、一共覆盖多少天），其余是比对结果。
    分开带着它们是为了让 `describe` 不必再拿到区间清单也能把话说全。
    """

    intervals: int = 0
    days: int = 0
    conflict_days: int = 0
    #: 当天在生效的判定带资讯抬升的天数。不进分母，但对得上账（见模块 docstring）。
    escalated: int = 0
    #: `escalated` 里「人工标注与当天的生效阶段一致」的天数。只用于让排除可审计。
    escalated_matched: int = 0
    #: 人工标成〈箱体震荡〉的日子。**不进分母**（ADR 0007），单列以便印出分母口径。
    boxed: int = 0
    counted: int = 0
    matched: int = 0
    differed: int = 0
    missing: int = 0
    misdirected: int = 0

    @property
    def min_rate(self) -> float:
        """达标线。**读配置面那一份**（第 164 条要求阈值进统一配置面）。"""
        return config.SHADOW.min_agreement_rate

    @property
    def rate(self) -> float | None:
        """一致率。分母是零（一天都比不了）时是 `None`，**不是 0**。

        0 与「无从谈起」在页面上必须能分开：前者是「全判错了」，后者是「还没有真值」。
        """
        if not self.counted:
            return None
        return self.matched / self.counted

    @property
    def met(self) -> bool:
        """第 163 条的第①条标准。约定了的系统性错向**一次就否掉**，不看一致率多高。"""
        rate = self.rate
        return rate is not None and rate >= self.min_rate and self.misdirected == 0


def day_regimes(intervals: Iterable[Interval]) -> dict[date, set[BaseRegime]]:
    """每一天被哪些人工档位覆盖过。并集按**日集合**算，不是各段天数相加。"""
    out: dict[date, set[BaseRegime]] = {}
    for interval in intervals:
        for day in each_day(interval.start, interval.end):
            out.setdefault(day, set()).add(interval.regime)
    return out


def fold_overlay(regimes: set[BaseRegime]) -> set[BaseRegime]:
    """把**叠加层**（高波动）从一天的档位集合里折叠掉。

    ## 为什么高波动不是普通档位

    CONTEXT.md 第 128 条把高波动定为「**叠加在当前状态上的保命层**」，与事件熔断同属
    `halt` 的触发源，而不是适用性结论。这个语义在人工标注上直接体现为：

        2021-04-14 ~ 2021-07-20  下行趋势
        2021-05-19                高波动      ← 519 暴跌，叠加在这段下行里

    两句话**不是矛盾**，是「这段在跌，其中某天崩了」。把它当冲突会丢掉那 136 天以外的
    全部信息，而冲突日不进分母——于是标注越细致，能参与比对的样本反而越少。

    折叠规则（只折叠一天**恰好**只有一个非高波动档位的情形）：

    - {高波动}                    → {高波动}：那天只有它，它自己就是结论
    - {高波动, 下行趋势}           → {下行趋势}：高波动被叠加、折叠
    - {高波动, 下行趋势, 箱体震荡} → 原样保留：去掉叠加层后仍有两天以上的判断在打架，
      那是**真的**自相矛盾（一个人不可能同时认为这段是下行又是箱体）

    **高波动单独出现时不折叠掉**：它是一条真实的人工结论，把它抹成空集会让那天既不算
    冲突也不进分母，等于白标。
    """
    if BaseRegime.HIGH_VOL not in regimes:
        return regimes
    rest = regimes - {BaseRegime.HIGH_VOL}
    if len(rest) <= 1:
        return rest or {BaseRegime.HIGH_VOL}
    return regimes


def conflicting_days(intervals: Iterable[Interval]) -> set[date]:
    """被**两个不同档位**同时覆盖、且**折叠叠加层之后仍然打架**的日子。

    同档位的重叠**不算冲突**：两段都说「那阵子是熊市」，那是互相佐证，只是标重了。

    **高波动不参与互斥判定**（见 `fold_overlay`）：它是叠加层，与底层档位并存是这个
    机制的正常形态，不是标注错误。

    这个函数是「录入时立刻报冲突」与 `tally` 里 `conflict_days` 的**同一份定义**——
    两处各写一遍，报的冲突与扣掉的天数迟早会对不上。`check_truth_csv.self_conflicts`
    也调 `fold_overlay`（它只是额外要知道是哪几行造成的），所以三处仍然同源。
    """
    return {
        day
        for day, regimes in day_regimes(intervals).items()
        if len(fold_overlay(regimes)) > 1
    }


def tally(
    intervals: Iterable[Interval],
    tags: Mapping[date, BaseRegime],
    *,
    in_force: Mapping[date, tuple[BaseRegime, BaseRegime]] | None = None,
) -> Agreement:
    """人工区间 × 历史量化标签 → 一次比对结果。**纯函数。**

    `tags` 只含**判得出来的日子**（`slice.build_tags` 的约定）：查不到的日子就是
    `missing`，不是「没这回事」。

    `in_force` 是「那天在生效的判定」，值 = `(基础阶段, 生效阶段)`（`judgement.in_force_days`
    的返回）。**缺席 ≠ 箱体震荡**：不在映射里就是「那天没有判定」，那些天照旧按纯量化口径
    比（历史区间本来就落在判定链开始之前）。映射里那些两值**不相等**的日子会被摘出分母，
    见模块 docstring。
    """
    intervals = list(intervals)
    covered = day_regimes(intervals)
    # 冲突判据与 `conflicting_days` 同源：**先折叠叠加层**再问「还剩几个档位」。
    conflicts = {
        day for day, regimes in covered.items() if len(fold_overlay(regimes)) > 1
    }
    in_force = in_force or {}

    matched = differed = missing = misdirected = escalated = escalated_matched = 0
    boxed = 0
    for day, regimes in covered.items():
        folded = fold_overlay(regimes)
        if len(folded) > 1:
            continue  # 冲突日：不进分母，见模块 docstring
        # 折叠之后取剩下的那个作为当天真值：`{高波动, 下行趋势}` 那天的人真值是
        # **下行趋势**，高波动只是叠加。用原集合取 `next(iter(...))` 会随机拿到高波动，
        # 于是「人工标了下行、算法也判下行」这种显然的匹配被记成不匹配——而错误方向
        # 还会把 `misdirected`（系统性错向）推成假阳性，一次就否掉整条成功标准①。
        truth = next(iter(folded))
        pair = in_force.get(day)
        if pair is not None and pair[0] != pair[1]:
            escalated += 1  # 那天机制用的不是纯量化结论（资讯抬升）：也不进分母
            if truth == pair[1]:
                escalated_matched += 1
            continue
        if truth is CALM:
            # 箱体区间**退出分母**（ADR 0007）。位置在冲突与抬升之后、缺口之前：
            # 那两层的理由是「这一天不能比」，比「这一天是什么档」更靠近输入侧。
            #
            # **只按人工标注判**，不看算法答了什么：口径问的是「拿哪些日子当分母」，
            # 不是「算法答对没答对」——照算法判的话，判对的日子进分母、判错的日子被
            # 剔出去，一致率会被系统性抬上去。
            boxed += 1
            continue
        got = tags.get(day)
        if got is None:
            missing += 1  # 缺口按不匹配计（进分母、不进分子）
            continue
        if got == truth:
            matched += 1
            continue
        differed += 1
        if truth in EXTREME and got == CALM:
            misdirected += 1

    return Agreement(
        intervals=len(intervals),
        days=len(covered),
        conflict_days=len(conflicts),
        escalated=escalated,
        escalated_matched=escalated_matched,
        boxed=boxed,
        counted=matched + differed + missing,
        matched=matched,
        differed=differed,
        missing=missing,
        misdirected=misdirected,
    )


def describe(data: Agreement) -> list[str]:
    """成功标准①这一段的**唯一**渲染器。体检页与 `/regime label list` 共用。

    返回的句子**不带缩进**，缩进由调用方加（两处的排版不一样，措辞必须一样）。第一行
    是「达标 / 未达标 + 一致率」，其余是支撑它的那几个计数。

    「同一件事两个说法」在这里代价格外高：一致率是**出 Shadow 的依据**，两个入口给出
    两个数的话，读到哪一个都会让人怀疑另一个。

    ## 分母口径必须印在一致率旁边（#67，ADR 0007）

    ADR 0007 定了「趋势区间计入分母、箱体区间不计入」。**这个口径不印出来，75.0% 就会被
    读成全档位的一致率**——而它只是趋势区间的。所以：

    - 有分母时，紧跟首行印一句 `分母口径`，明说哪一类日子计入、哪一类不计入；
    - 箱体天数非零时，再印出这个数：读者要能自己验算「分母为什么比覆盖天数少」。

    这一句**与箱体天数是否为零无关**（只看有没有分母）：口径本身是常设的，不是这天才
    成立的；只在有箱体时才说，会让没箱体的那天读成「没有这个口径」。
    """
    if not data.intervals:
        return [
            f"① 判定与人工标注的一致率 ≥ {data.min_rate:.0%}：无法判定——"
            "尚未录入任何人工标注区间，按未达标计（录入入口：/regime label add）",
        ]

    rate = data.rate
    head = f"① 判定与人工标注的一致率 ≥ {data.min_rate:.0%}："
    if rate is None:
        # 「按未达标计」这几个字必须在这儿：`met` 确实是 False，页面不能只说「无法判定」，
        # 否则读的人会以为这一条不算数。
        head += "无法判定——按未达标计"
    else:
        head += (
            f"{'达标' if data.met else '未达标'}——"
            f"{data.matched}/{data.counted} 天一致（{rate:.0%}）"
        )

    lines = [head]
    if rate is not None:
        # 分母口径**紧跟首行**：它是「上面那个百分比是什么的一致率」的答案，隔开一行就会
        # 被读成另一个话题的脚注。
        #
        # **措辞不能只说「趋势区间」**：分母里除了上行 / 下行，还可能有人工**单独**标的
        # 〈高波动〉日（`fold_overlay` 明确保留 `{高波动}`，它不进 `boxed`）。只写「趋势
        # 区间的一致率」时，分母含高波动日的那份标注会让这句话变成假的——而它恰恰是用来
        # 防误读的那一句。所以这里按「谁出分母」正面说，不按「谁进分母」枚举。
        lines.append(
            "   · 分母口径：除箱体区间外的日子计入、箱体区间不计入（ADR 0007）"
            f"——上面的 {rate:.0%} 不含箱体，不是全档位的一致率"
        )
    if data.boxed:
        lines.append(
            f"   · 另有 {data.boxed} 天人工标〈箱体震荡〉，不计入分母"
            "（箱体不可机械识别，算进分母会让一致率说不出是哪一类日子的一致率）"
        )
    if data.conflict_days:
        # 冲突不是算法的毛病，但它解释了「为什么 23 天只比了 18 天」。
        lines.append(
            f"   · 另有 {data.conflict_days} 天被两段不同档位的人工区间同时覆盖，"
            "作为冲突日不计入分母"
        )
    if data.escalated:
        line = (
            f"   · 另有 {data.escalated} 天当天在生效的判定带资讯抬升"
            "（那几天机制用的不是纯量化结论），不计入分母"
        )
        if data.escalated_matched:
            # 这一句是给「排除」留的底：只说「摘掉了 N 天」，那几天就成了看不见底的黑洞；
            # 报出其中判对的天数，人才敢相信排除不是因为不好看。
            line += f"；其中 {data.escalated_matched} 天人工标注与当天的生效阶段一致"
        lines.append(line)
    if rate is None:
        # 没有分母时要**说清是哪一种没有**：这句话是人唯一能拿到的解释，而几种原因
        # （标注自相矛盾 / 那几天不能比 / 全是箱体区间）对应的是完全不同的下一步动作。
        why = []
        if data.conflict_days:
            why.append("互相冲突的档位")
        if data.escalated:
            why.append("资讯抬升日")
        if data.boxed:
            why.append("箱体区间")
        because = f"全部落在{'或'.join(why)}上，" if why else ""
        lines.append(
            f"   · 录入的 {data.intervals} 段（{data.days} 天）{because}"
            "没有一天可用于比对"
        )
    else:
        detail = f"{data.differed} 天不一致"
        if data.missing:
            detail += f"（其中 {data.missing} 天算法当天没有输出，按不匹配计）"
        lines.append(f"   · 分母 {data.counted} 天：{data.matched} 天一致、{detail}")
    if data.misdirected:
        lines.append(
            f"   · **系统性错向 {data.misdirected} 天**：人工标〈高波动〉或〈下行趋势〉"
            "而算法判〈箱体震荡〉——哪怕一次也算不达标（第 164 条）"
        )
    return lines
