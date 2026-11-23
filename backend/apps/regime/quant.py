"""量化判定核心（第①段单元 3）：kline + 参数 → 基础阶段 + 依据数字。

**这里只有一根轴。** 生效阶段 = 基础阶段被资讯抬升后的值，而资讯那根轴要等单元 5。
本文件产出的是 CONTEXT.md 所谓「纯量化口径」，它同时是两件事的唯一实现：

- 每日实时判定的量化部分（单元 4 取序列最后一项）；
- **历史切片用的标签**（单元 6 对全窗口逐日调用）。

这两件事必须是同一个函数。CONTEXT.md 明确记了「切片用的是纯量化口径的历史标签，与
实时判定（量化+资讯）不是同一个函数」——那句话的意思是**资讯不允许进历史标签**
（历史资讯不可重建），不是「两份量化实现」。若这里只给「最后一天」写一个判定函数，
单元 6 就得再写一遍滚动循环，于是同一个阈值出现两处实现，这是本设计反复在防的那类
错误。所以入口是 `label_series`（逐日、与输入等长、全量），`latest_label` 只是它的
尾巴。

## 纯函数

不碰 DB、不读时钟、不写日志、不看全局状态：输入决定输出。判定参数由调用方显式传入
（默认取统一配置面的当前值），因此「同一段 kline + 同一套参数 ⇒ 同一条结论」可以被
测试直接钉住，历史标注也才谈得上可复现。

**用 float 而不是 Decimal**：这里算的是统计量（分位数、ATR%、比值），不是金额。金额
口径（下单、盈亏、滑点）必须用 Decimal，但那不是这一层的职责。落库的 `DailyCandle`
仍是 Decimal，转 float 只发生在本文件内部。

## 两段：算数（`metrics_series`）与定档（`_classify`）

逐日判定被刻意拆成两步，因为「周期」与「波动」两个维度的分类器要共用同一批中间量：

1. `metrics_series` **只算数**：ATR、ATR%、ATR% 分位、快慢 EMA、斜率、间距，连同波动
   代理要用的跳空 / 放量倍数 / ATR 环比，逐日产出一个 `DayMetrics`。它不含任何档位，
   也不替调用方判断「能不能定档」——算不出来就是 `None`，理由与下面「数据不足」一节相同。
2. `_classify` **只定档**：吃一个 `DayMetrics`，先验「三个条件齐了吗」，再按优先级给
   `BaseRegime`。它不看 K 线，也不自己算数。

`label_series` 是这两步的串联，仍然是对外唯一入口（实时判定与历史标注共用）。拆开的
收益在下一步：加一个分类器只需再写一个吃 `DayMetrics` 的函数，不必复制一遍算数——复制
出来的第二份算数迟早会和第一份漂开，而两边的结论都会看着很正常。

波动代理的三个量（`gap` / `volume_ratio` / `atr_jump`）当前**只算不用**：它们进结构体
是为了让波动维的分类器直接可取，本阶段不参与定档。K 线行里没有 `open` / `volume`
时它们就是 `None`——「说不出来就说说不出来」，不是 0。

## 周期维自成一体（#64）

`_classify` 出的是 `BaseRegime`（四档兼容层），`_classify_cycle` 出的是 `Cycle`。
**两者已经不是同一件事了**，这是 #64 之后本模块最重要的结构变化：

- `_classify` 仍按固定优先级走，高波动排第一——它回答「此刻该不该保命」，服务的是
  停机链、池化与切片。这一层本票**刻意不动**，那是 #65 / #66 的范围。
- `_classify_cycle` 回答「往哪走」，两条独立路径（间距 / 强度×一致性），横盘要显式
  证明，判不出来落 `unclear`。

于是在高风险日会出现 `regime=high_vol` 而 `cycle=bear` 这类组合——**那不是矛盾**，
正是 ADR 0007 要的「两维并存、互不覆盖」。反过来，`project_to_axes` 那套兼容投影
仍然存在且仍然正确（四档 → 两维），只是它不再是 `cycle` 的来源：拿投影去顶替独立
判定，会在写入路径上把「缓慢趋势兜底成横盘」这个缺陷悄悄装回来。

## 四档与优先级

高波动 > 下行趋势 > 上行趋势 > 箱体震荡，互斥且固定（`PRIORITY`）。**箱体震荡是兜底**
（这一句只描述 `BaseRegime` 这个**兼容层**——周期维 `cycle` 自 #64 起已不再兜底，
见下节），
所以它不是一个「检出了箱体」的结论，而是「既非高波动、也无趋势」的剩余。这一点有直接
后果：箱体判定（`detect_box_range`）**不参与**档位归属——检不检出都落到箱体震荡。它的
用途是给日报写依据（「判为箱体，且确实检出 [a, b]」vs「判为箱体，但未见成形箱体」），
参数形状等日报需要时才定（见 config.py 的分组清册）。**不要把它改成一道闸**：那样会多出
一个「既非四档也不成形箱体」的第五态，与「四档固定枚举、互斥」直接冲突。

## 数据不足 ≠ 某一种阶段

分位窗口没攒满、或任何一项指标还是 NaN 的日子，`regime` 是 `None`，**不是「箱体震荡」**。
把它算成箱体震荡等于拿一个安静的假标签去喂切片，而切片正是拿这些标签停策略的地方。
与单元 1「失败必须能与『没有新数据』区分」是同一条纪律：**说不出来就说说不出来**。
`None` 的日子不是失败，是「还没有资格判定」——调用方（单元 4）另行区分取数失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np

from apps.regime.config import JUDGEMENT, JudgementConfig
from apps.strategy_engine.indicators import atr as _atr
from apps.strategy_engine.indicators import ema as _ema
from apps.strategy_engine.indicators import sma as _sma


class BaseRegime(str, Enum):
    """基础阶段：四档固定枚举。

    值用 ascii slug（与 `apps/datasource/base.py`、`apps/agent/frame_manager.py`
    的枚举同一约定）：落库、日志、查询条件里都是干净的标识符，中文只在展示层出现。
    """

    HIGH_VOL = "high_vol"
    DOWNTREND = "downtrend"
    UPTREND = "uptrend"
    RANGE = "range"

    @property
    def display(self) -> str:
        """日报 / 告警里的中文名。展示与取值分开，避免为了好看去改落库契约。"""
        return _DISPLAY[self]


_DISPLAY = {
    BaseRegime.HIGH_VOL: "高波动",
    BaseRegime.DOWNTREND: "下行趋势",
    BaseRegime.UPTREND: "上行趋势",
    BaseRegime.RANGE: "箱体震荡",
}

#: 周期维的展示名。与 `BaseRegime` 同一约定：**取值是落库契约**，中文只在展示层出现。
CYCLE_DISPLAY = {
    "bull": "上行周期",
    "bear": "下行周期",
    "sideways": "横盘",
    "unclear": "判不出周期",
}

#: 波动维的展示名。`normal` 说的是「没有事件波动」，不是「波动很低」——本版还没有
#: 波动程度的判据，这个措辞必须让读的人不会把它当成结论。
VOLATILITY_DISPLAY = {
    "normal": "无事件波动",
    "event_spike": "事件波动",
}

#: 判定优先级，高到低；`RANGE` 是兜底因而居末。改这个顺序等于改机制语义，不要顺手动。
#:
#: **#66 之后它只剩兼容层的用途**：切片与池化的格子轴换成了 `CYCLE_CELLS`（按 cycle
#: 分三格），`PRIORITY` 继续服务四档兼容读面（判定记录的 `effective_regime`、真相比对、
#: 矩阵旧列序）。拿它当格子的维度就会把「高波动单独占一格」这个缺陷装回来。
PRIORITY: tuple[BaseRegime, ...] = (
    BaseRegime.HIGH_VOL,
    BaseRegime.DOWNTREND,
    BaseRegime.UPTREND,
    BaseRegime.RANGE,
)


class Cycle(str, Enum):
    """**周期维**（慢变量）：往哪走。

    与 `BaseRegime` 的关系是「新结构 / 旧兼容层」，但**自 #64 起这一维不再由四档投影
    出来**：它有独立的判据（`_classify_cycle` 的两条路径 + 显式横盘），四个取值全部会
    实际产出。`project_to_axes` 仍在，但它只服务于兼容读面与迁移回填，不再决定
    `DayLabel.cycle`。

    两维与四档因此**允许不一致**，且不一致有意义：`base=high_vol + cycle=bear` 正是
    ADR 0007 要的「两维并存、互不覆盖」。
    """

    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    UNCLEAR = "unclear"

    @property
    def display(self) -> str:
        """展示层的中文名。与 `BaseRegime.display` 同一约定：取值不动中文。"""
        return CYCLE_DISPLAY[self.value]


#: 切片与池化的**格子轴**（#66）：按 cycle 分三格，bull → bear → sideways。
#:
#: 为什么是这一组常量而不是 `PRIORITY`：
#:
#: - **波动不进格子。** ADR 0007 确认高波动是叠加状态，不是「这条策略在高波动阶段表现
#:   如何」的一格。让高波动单独占一格会让那一格的样本极稀（本就不该做适用性判断），
#:   而 `PRIORITY` 把 `HIGH_VOL` 排第一位，照它遍历就是在给那一格攒证据。
#: - **`unclear` 也不进格子。** 它说的是「判不出方向」，不是第三个方向。把它当第四格
#:   会让「判不出来」暴露成一种可比较的适用性，而 `#64` 已经把它定为「按没有结论计」。
#:
#: 顺序即矩阵列序：上行 → 下行 → 横盘。这个顺序**没有判定含义**（三格互斥且平等），
#: 它只是让页面上固定的列序由后端给，前端不必自己再排一遍。
CYCLE_CELLS: tuple[Cycle, ...] = (
    Cycle.BULL,
    Cycle.BEAR,
    Cycle.SIDEWAYS,
)

#: `CYCLE_CELLS` 的取值集合。判定「这一格是不是格子轴上的合法键」时用它，比逐个比字面量
#: 安全：加一格就自动进集合。
CYCLE_CELL_VALUES: frozenset[str] = frozenset(c.value for c in CYCLE_CELLS)


class Volatility(str, Enum):
    """**波动维**（快变量叠加层）：此刻要不要保命。

    只有两个取值，且**只有 `EVENT_SPIKE` 意味着叠加层生效**。它与周期维并存而不是
    互斥——`bull + event_spike` 是牛市暴跌日，这正是把两维拆开的全部理由。

    `NORMAL` 是「没有事件波动」，**不是**「波动很低」：本版还没有波动程度的判据，
    把它读成「平静」会让 #65 落地前的每一条 `NORMAL` 都被当成结论。
    """

    NORMAL = "normal"
    EVENT_SPIKE = "event_spike"

    @property
    def display(self) -> str:
        return VOLATILITY_DISPLAY[self.value]


#: `volatility_source` 的取值。**取值即落库契约**，改名等于让新旧行出现两套口径。
#:
#: - `legacy_mapping`：迁移按 `base_regime` 回填的历史行（#63）。那不是判据，是投影。
#: - `compatible_projection`：`volatility` 仍由四档投影而来的那段过渡期写下的行（#63）；
#:   新写入的行不再产生这个值，但历史行的读法不能变，所以常量留着。
#: - `proxy`：**#65 起**写入的行。波动维由事件驱动**价格代理**判出来（跳空 / 放量 /
#:   ATR 骤升 / 单日大幅）——它是判据，但不是真实资讯确认，所以名字必须带上「代理」。
#:   三个取值必须可区分，否则「那天的高波动是判出来的、映射来的、还是消息面确认的」
#:   这个问题永远答不上来（#67 的日报要把它印出来）。
VOLATILITY_SOURCE_LEGACY = "legacy_mapping"
VOLATILITY_SOURCE_PROJECTION = "compatible_projection"
VOLATILITY_SOURCE_PROXY = "proxy"
#: 波动维由**资讯抬升**置上的（资讯说「进入风险状态」，而价格代理本身没判出事件）。
#: 与 `proxy` 分开是必需的：#67 的日报要回答「今天的高波动来自价格代理还是消息面」，
#: 两者都写 `proxy` 的话，那个问题永远答不上来。代理判据本来就有假阳性，再把「消息面
#: 说的」混进去，「每个高波动日都有新闻佐证」这个误读就被焊死了。
VOLATILITY_SOURCE_NEWS = "news_escalation"

#: 四档 → 两维。**唯一一处投影表**，两个方向都从它派生（反向表由它生成，不另写一份）。
#:
#: `high_vol` 的 cycle 是 `None` 而不是某个猜出来的方向：旧结构里 HIGH_VOL 排在
#: `PRIORITY` 第一位，那天的周期信息**在判据里就不存在**。如实留空是本票的硬要求
#: （issue #63：「旧结构里波动吃掉了周期，如实留空而不是编一个」）。
_TO_AXES: Mapping[BaseRegime, tuple[Cycle | None, Volatility]] = {
    BaseRegime.UPTREND: (Cycle.BULL, Volatility.NORMAL),
    BaseRegime.DOWNTREND: (Cycle.BEAR, Volatility.NORMAL),
    BaseRegime.RANGE: (Cycle.SIDEWAYS, Volatility.NORMAL),
    BaseRegime.HIGH_VOL: (None, Volatility.EVENT_SPIKE),
}


def project_to_axes(
    regime: BaseRegime | None,
) -> tuple[Cycle | None, Volatility | None]:
    """四档 → `(cycle, volatility)`。未判定日（`None`）两维都是 `None`。

    两维**同进退**：判不出来的日子不许出现「有一维、没另一维」的半截结论。填个
    `sideways + normal` 就是拿一个安静的假结论去喂切片。
    """
    if regime is None:
        return (None, None)
    return _TO_AXES[regime]


def project_to_base_axes(
    cycle: Cycle | str | None, volatility: Volatility | str | None
) -> BaseRegime | None:
    """`(cycle, volatility)` → 四档。**还原不出来的组合在半截数据上抛，在完整数据上塌。**

    这是反向投影，唯一用途是把两维读回本版既有的四档世界（读面兼容、历史回填校验）。
    它的错误面分两类，处理方式不同：

    - **半截数据 → 抛**。`cycle is None` 配 `volatility=normal` 是「没有方向、也没有
      事件波动」，旧四档里没有这一格；只有一维为空更是写入路径漏了一半。这些都不是
      「某种状态」，是数据坏了——猜一个档位下去，切片会当真。
    - **完整数据但四档表达不了 → 塌，不抛**（#65 改了这一点）。`bull`/`bear`/
      `sideways`/`unclear` 配 `event_spike` 是**两维判定的正常产物**（牛市暴跌日），
      不是矛盾。投影的职责是给兼容读面一个答案，塌法是保留 `high_vol`——保命这件事必须
      保留（它决定要不要停全场），丢掉的只是方向的分辨率，而方向在 `cycle` 列里原样躺着。

      **#63 时这里抛是对的**：那时两维只是四档的投影，出现这个组合说明投影表自相矛盾。
      现在两维是真判定，再抛就成了「一有事件波动日，整条历史标注链挂掉」。

    `UNCLEAR + normal`（有结论、结论是判不出来）还原成 `None`：与未判定同形。
    """
    if cycle is None and volatility is None:
        # 两维同时为空 = 未判定（数据不够，还没有资格下结论）。这正是 `regime=None`
        # 的形状，还原得出来，而且是**唯一**允许两维同空的组合。
        return None

    cycle = Cycle(cycle) if cycle is not None else None
    volatility = Volatility(volatility) if volatility is not None else None

    if volatility is None:
        # 只有一维为空是半截数据：那不是「某种状态」，是写入路径漏了一半。
        raise ValueError("缺少波动维，无法还原四档——两维必须同时存在或同时为空")
    if cycle is None:
        if volatility is Volatility.EVENT_SPIKE:
            return BaseRegime.HIGH_VOL
        raise ValueError("cycle 为空且波动正常：旧四档里没有这一格")
    if volatility is Volatility.EVENT_SPIKE:
        # 有方向 + 事件波动（`bull + event_spike` = 牛市暴跌日）。**#65 起这是正常产出**，
        # 不再抛：投影是兼容读面，它的职责是把两维塌成四档，而四档表达不了「又有方向
        # 又高波动」——塌法是保留保命档（`high_vol`），丢掉方向。方向没有丢，它在
        # `cycle` 那一列里原样躺着，投影丢掉的只是这份**兼容视图**的分辨率。
        #
        # #63 当时抛是对的：那时两维还只是四档的投影，「bull + event_spike」出现意味着
        # 投影表自相矛盾。现在两维是真判定，这个组合是它的正常产物，再抛就成了「一有
        # 事件波动日，整条历史标注链挂掉」。
        return BaseRegime.HIGH_VOL
    if cycle is Cycle.UNCLEAR:
        return None
    for regime, (mapped_cycle, mapped_vol) in _TO_AXES.items():
        if mapped_cycle is cycle and mapped_vol is volatility:
            return regime
    raise ValueError(f"没有对应的四档：cycle={cycle}, volatility={volatility}")


def project_to_base(label: "DayLabel") -> BaseRegime | None:
    """一条标签 → 四档。`label_series` 的兼容投影，逐日等于是本票的验收判据。"""
    return project_to_base_axes(label.cycle, label.volatility)


@dataclass(frozen=True)
class DayMetrics:
    """某一日的**中间量**：定档所需要的一切数字，一个档位都不含。

    这个结构体存在的唯一理由是「两个维度的分类器要共用同一批算数」。它因此必须是
    **纯算数**的：不判断够不够、不给默认值、不暗示某个量是「好」还是「坏」。可算的
    算出来，算不出来的留 `None`（不是 0，也不是 NaN——NaN 会往判定里漏水，
    `None` 才是「这个统计量今天不存在」的显式形态）。

    `quantile_sample` 是**样本数**而不是判定结论：它等于窗口里有限的 ATR% 个数。
    「窗口够不够满」这个判断体现在 `atr_pct_rank` 上——窗口没满它就没有值（`None`），
    而不是填一个基于残缺样本的分位。`quantile_sample` 留在结构里是为了让事后能回答
    「那天为什么判不出来」，不参与就绪判断。

    ## 波动代理的三个量（本阶段只算不用）

    `gap` / `volume_ratio` / `atr_jump` 属于下一步的 volatility 维，本阶段**不参与**
    定档，进结构体只是为了让那个分类器不必再算一遍。它们依赖 K 线行里的 `open` /
    `volume`；调用方没给（或给了不可解析的值）时为 `None`——行情源缺量能数据是常态，
    编一个 1.0 会让「没有量能」与「量能平稳」长得一模一样。
    """

    date: date

    # 波动轴
    atr: float | None
    atr_pct: float | None
    atr_pct_rank: float | None
    quantile_sample: int

    # 趋势轴
    ema_fast: float | None
    ema_slow: float | None
    ema_slope: float | None
    separation: float | None

    # 周期维的两条独立通道（#64）。它们与上面那几个量是**同一批算数**的产物，
    # 进结构体是为了让 `_classify_cycle` 不必重算——重算出来的第二份迟早会和第一份
    # 漂开，而两边的结论都会看着很正常。
    #
    # `slope_strength` = |ema_slope| / ATR，量「单位波动走了多远」；它与 `separation`
    # 的分工是路径二与路径一：间距说「两条均线拉开了吗」，强度说「快线自己走得快不快」。
    #
    # `slope_consistency` = 回看窗口内斜率符号与当日同向的占比（0~1）。它回答的是
    # 「这个方向是持续的，还是今天才翻的」——单独看强度分辨不了这两件事，而那正是
    # 「有动能无共识」的日子被判成趋势的来源。
    slope_strength: float | None
    slope_consistency: float | None

    # 波动代理（事件驱动判定的四条证据，#65）。任一越阈值即 `event_spike`。
    gap: float | None
    volume_ratio: float | None
    atr_jump: float | None
    #: 单日大幅：`|close_t / close_{t-1} - 1|`。取绝对值——它问的是「今天走没走出
    #: 一根大阴大阳」，方向由周期维回答，两维各自只答自己的问题。
    move: float | None


@dataclass(frozen=True)
class DayLabel:
    """某一天的基础阶段，连同**决定它的那几个数字**。

    数字全部保留的理由是 CONTEXT.md 的「每条停用决策都要能回答依据什么」：半年后要解释
    「那天为什么判成高波动」，答案必须在这一行里，而不是靠当时的日志或重跑一遍。参数侧
    由判定记录内嵌的配置快照（`config.snapshot`）补齐，两者合起来构成完整依据。

    未判定日（`regime is None`）不填任何推断值：可算的算（`atr_pct` 等），不可算的留
    `None`（`atr_pct_rank` 需要满窗口，`None` 表示「这个统计量今天不存在」，而不是 0）。

    `metrics` 是那批中间量本身，与逐字段属性指向同一个对象。两处并存不是冗余：
    落库 / 序列化侧要的是扁平字段（旧键一个都不能少），分类器要的是一个整体。
    """

    date: date
    regime: BaseRegime | None
    metrics: DayMetrics

    #: 两维。**#65 之后两者都由独立判据产出，与 `regime` 无关**：
    #:   - `cycle` 走 `_classify_cycle` 的两条路径 + 显式横盘；
    #:   - `volatility` 走 `_classify_volatility` 的四条事件证据。
    #: 未判定日两维**同时**是 `None`；`cycle` 判不出来时是 `UNCLEAR`，不是 `None`。
    #: `regime`（四档）仍是兼容层，它自己的优先级里保命档排第一——两维与它允许不一致，
    #: 且不一致正是 ADR 0007 要的形状：`base=high_vol + cycle=bear` 是牛市里的暴跌日。
    cycle: "Cycle | None"
    volatility: "Volatility | None"

    # 波动轴
    atr: float | None
    atr_pct: float | None
    atr_pct_rank: float | None
    quantile_sample: int

    # 趋势轴
    ema_fast: float | None
    ema_slow: float | None
    ema_slope: float | None
    separation: float | None

    #: 周期维那两条通道的中间量（#64）。落库要的扁平字段与 `metrics` 指向同一批数，
    #: 所以这里也各留一格——事后要解释「那天为什么判 unclear」，答案必须在这一行里。
    slope_strength: float | None
    slope_consistency: float | None

    #: 波动维的单日大幅（#65）。与另三个代理量一样随标签带出来，好让「那天为什么判
    #: event_spike」能在这一行里答上，而不是靠重跑一遍。它没有对应的落库列（另外三个
    #: 也没有）——依据数字整体走 `evidence["quant"]`。
    move: float | None = None

    @property
    def judged(self) -> bool:
        return self.regime is not None


def percentile_rank(window: np.ndarray, value: float) -> float:
    """`value` 在 `window` 中的经验分位（#(x <= value) / n，含 `value` 自身）。

    三条性质是刻意的：

    - **含当日**。CONTEXT.md 说的是「ATR% 分位」，其含义是「今天的波动在过去一年里排第
      几」，所以要把它自己算进样本里；365 天里最高的一天分位就是 1.0。
    - **`<=` 而非 `<`**：值相等时计入更高分位。币圈日线的 ATR% 撞上同一数字的概率极低，
      但并列一旦发生，两个方向里**只有往「更高波动」算才是保守的**。这条与「不确定时往
      保守倒」一致。
    - **不做插值**。分数秩是整数计数，跨机器、跨 numpy 版本完全一致；插值分位（pandas 的
      `interpolate` 之类）会引入实现定义的自由度，而这里的分位是整套阈值的标定基准。
    """
    if window.size == 0:
        raise ValueError("percentile_rank 的窗口不能为空")
    return float(np.count_nonzero(window <= value)) / float(window.size)


def _require_date(row: Mapping[str, Any], index: int) -> date:
    """取行上的日期。

    日期是必填的，不是可选的：标签序列的整个用途就是「日期 → 阶段」，单元 6 要按开仓
    日期把它 join 到逐笔交易上。缺了日期还能悄悄产出一条标签，那条标签将永远 join 不上
    而其存在本身看起来完全正常。
    """
    value = row.get("date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(
        f"第 {index} 根 kline 缺少可用的 date（需 date/datetime），当前 {value!r}"
    )


def _normalize(candles: Sequence[Mapping[str, Any]]) -> tuple[list[date], list[dict]]:
    """把任意数值类型（Decimal / float / str）的行统一成 float 行，供指标库消费。

    `high`/`low`/`close` 是**必需**的（缺了它什么档都定不出来，静默补一个值只会
    产出一条看着正常的假标签）。`open`/`volume` 是**可选**的：行情源不给量能是常态，
    而它们现在只喂波动代理、不参与定档。缺或不认识就留 `None`，让下游的
    `volume_ratio`/`gap` 变成 `None`——不能编成 0 或 1.0，那样「没有数据」与「量能
    平稳」会长得一模一样。
    """
    dates: list[date] = []
    rows: list[dict] = []
    for index, row in enumerate(candles):
        dates.append(_require_date(row, index))
        try:
            normalized = {
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 根 kline 的 high/low/close 无法解析") from exc
        for name in ("open", "volume"):
            normalized[name] = _optional_float(row.get(name))
        rows.append(normalized)
    return dates, rows


def _optional_float(value: Any) -> float | None:
    """可选数值列的宽松解析：读不出来就是 `None`，不抛。

    只用于 `open`/`volume` 这类**不参与定档**的列。它们读不出来的后果只是某个代理量
    算不出来（`None`），而不是一条错误标签——为此让整条标注序列失败是把可选项当成了
    必选项。
    """
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _pad_to(array: np.ndarray, size: int) -> np.ndarray:
    """把指标库「算不出来就返回空数组」的返回值补成等长的 NaN 序列。

    底层 `compute_*` 在样本不足时返回 `np.array([])` 而不是全 NaN——对一次性的策略信号
    计算无所谓，对逐日序列则是个陷阱：索引会错位，或者干脆 IndexError。这里统一把它们
    还原成 NaN，于是「样本不足」就落进和「窗口没攒满」同一个显式状态（`regime is None`），
    而不是变成另一种要单独处理的形态。
    """
    if array.size == size:
        return array
    padded = np.full(size, np.nan, dtype=np.float64)
    return padded


def metrics_series(
    candles: Sequence[Mapping[str, Any]],
    params: JudgementConfig = JUDGEMENT,
) -> list[DayMetrics]:
    """逐日算出**中间量**。输出与输入**等长同序**，一天一条；一个档位都不含。

    这是「算数」那一段的全部：ATR、ATR%、ATR% 分位、快慢 EMA、斜率、间距，外加波动
    代理要用的跳空 / 放量倍数 / ATR 环比。定档在 `_classify` 里，两者分开的理由见
    模块 docstring：两个维度的分类器要共用这一批算数，复制一遍迟早会漂。

    只使用 `candles[i]` 及其之前的数据——没有任何前视，与 `label_series` 同一条约束。
    算不出来的量一律 `None`（不是 0、不是 NaN），由分类器决定这够不够定档。
    """
    if not candles:
        return []

    dates, rows = _normalize(candles)
    n = len(rows)

    closes = np.array([r["close"] for r in rows], dtype=np.float64)
    highs = np.array([r["high"] for r in rows], dtype=np.float64)
    lows = np.array([r["low"] for r in rows], dtype=np.float64)

    # 三数组形式显式传 period：K 线 dict 形式下 `atr(history, period)` 在同时给了
    # `period=` 关键字时会静默取默认值，是个不该踩的坑。
    atr = _pad_to(_atr(highs, lows, closes, period=params.atr_period), n)
    ema_fast = _pad_to(_ema(closes, params.ema_fast_period), n)
    ema_slow = _pad_to(_ema(closes, params.ema_slow_period), n)

    # ATR% 而非 ATR 绝对值：BTC 价格量级跨四年变化几十倍，绝对波幅的分位会把早年
    # 那段低价的平静行情算成「波动很小」，而它按比例并不小。归一化是分位可比的前提。
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_pct = atr / closes

    qw = params.quantile_window_days
    lookback = params.ema_slope_lookback_days

    # 满窗口判定用前缀和：窗口内有限值的个数是否等于窗口长度。
    finite_pct = np.isfinite(atr_pct).astype(np.int64)
    prefix = np.concatenate([[0], np.cumsum(finite_pct)])

    opens = np.array([r["open"] if r["open"] is not None else np.nan for r in rows])
    volumes = np.array(
        [r["volume"] if r["volume"] is not None else np.nan for r in rows]
    )
    volume_ma = _pad_to(_sma(volumes, params.volume_ma_period), n)

    series: list[DayMetrics] = []
    for i in range(n):
        start = max(0, i + 1 - qw)
        sample = int(prefix[i + 1] - prefix[start])

        available = dict(
            atr=_finite_or_none(atr[i]),
            atr_pct=_finite_or_none(atr_pct[i]),
            ema_fast=_finite_or_none(ema_fast[i]),
            ema_slow=_finite_or_none(ema_slow[i]),
        )
        slope_idx = i - lookback
        slope = None
        if (
            slope_idx >= 0
            and np.isfinite(ema_fast[i])
            and np.isfinite(ema_fast[slope_idx])
        ):
            slope = float(ema_fast[i] - ema_fast[slope_idx])

        separation = None
        if (
            available["atr"] is not None
            and available["atr"] > 0
            and available["ema_fast"] is not None
            and available["ema_slow"] is not None
        ):
            # 以 ATR 为单位：这个比值无量纲，因此门槛不随价格量级漂移。
            separation = float(
                (available["ema_fast"] - available["ema_slow"]) / available["atr"]
            )

        # 周期维的两条通道（#64）。都从上面已经算好的 `slope` / `atr` / `ema_fast`
        # 派生，不再重算指标——这正是 `metrics_series` 只算数、不算档的意义。
        strength = None
        if slope is not None and available["atr"] is not None and available["atr"] > 0:
            strength = float(abs(slope) / available["atr"])

        consistency = _slope_consistency(
            ema_fast, i, lookback, params.cycle_consistency_window
        )

        window_full = i + 1 >= qw and sample == qw
        rank = None
        if (
            window_full
            and available["atr_pct"] is not None
            and available["atr_pct"] > 0
        ):
            rank = percentile_rank(atr_pct[start : i + 1], atr_pct[i])

        series.append(
            DayMetrics(
                date=dates[i],
                atr_pct_rank=rank,
                quantile_sample=sample,
                ema_slope=slope,
                separation=separation,
                slope_strength=strength,
                slope_consistency=consistency,
                gap=_gap_ratio(opens[i], closes[i - 1]) if i >= 1 else None,
                volume_ratio=_volume_ratio(volumes[i], volume_ma[i]),
                atr_jump=_atr_jump(atr[i], atr[i - 1]) if i >= 1 else None,
                move=_move_ratio(closes[i], closes[i - 1]) if i >= 1 else None,
                **available,
            )
        )

    return series


def _gap_ratio(open_: float, previous_close: float) -> float | None:
    """跳空幅度：`|open_t / close_{t-1} - 1|`。

    取绝对值而不是带符号：波动代理问的是「有没有跳」，不是「往哪跳」——方向由周期维
    回答，两个维度各自只答自己的问题。
    """
    if not (np.isfinite(open_) and np.isfinite(previous_close)):
        return None
    if previous_close <= 0:
        # 前一日收盘非正只能来自坏数据；除下去会得到 inf 再被吞成 nan，看起来像「没跳空」。
        return None
    return float(abs(open_ / previous_close - 1.0))


def _move_ratio(close: float, previous_close: float) -> float | None:
    """单日大幅：`|close_t / close_{t-1} - 1|`。

    与跳空共用同一套「前一天收盘为坏数据就返 None」的纪律：除下去会得到 inf 再被吞成
    nan，看起来像「没走大行情」——而它实际上是「这一天的数据不足以下任何结论」。
    """
    if not (np.isfinite(close) and np.isfinite(previous_close)):
        return None
    if previous_close <= 0:
        return None
    return float(abs(close / previous_close - 1.0))


def _volume_ratio(volume: float, volume_ma: float) -> float | None:
    """放量倍数：当日量能 / 均量。均量为 0 或算不出来时是 `None`。"""
    if not (np.isfinite(volume) and np.isfinite(volume_ma)):
        return None
    if volume_ma <= 0:
        return None
    return float(volume / volume_ma)


def _atr_jump(atr: float, previous_atr: float) -> float | None:
    """ATR 环比：`ATR_t / ATR_{t-1}`。前一日 ATR 为 0（停牌、重复 K 线）时是 `None`。"""
    if not (np.isfinite(atr) and np.isfinite(previous_atr)):
        return None
    if previous_atr <= 0:
        return None
    return float(atr / previous_atr)


def _slope_consistency(
    ema_fast: np.ndarray,
    index: int,
    lookback: int,
    window: int,
) -> float | None:
    """回看窗口内「斜率符号与当日同向」的占比；窗口不够满时是 `None`。

    这是周期维路径二的第二道门，回答的是**方向是不是持续的**。没有它，单看
    `|slope| / ATR` 会把「今天猛地动一下、之前一直横着」也算成趋势；有了它，那种
    日子落 `unclear`——不是箱体，因为强度明明够。把「有动能无共识」判成横盘，正是
    旧结构那 207 天系统性错向的主要形态之一。

    ## 只用 `[i - window + 1, i]` 这一段

    窗口内每个 j 的斜率都由 `ema_fast[j]` 与 `ema_fast[j - lookback]` 决定，两者都
    不晚于 j，因此**没有前视**。这一点与 `label_series` 的同一条约束一致：某天的
    标签只能用那天及之前的数据，否则切片会「预知」那段行情。

    ## 窗口不满就是 `None`，不是 1.0

    返 `None` 让调用方落 `unclear`。填 1.0 会把「还看不出方向有没有持续性」与
    「方向一直很一致」写成同一个数，而前者本来该说「判不出来」。

    ## 分母是**实际采到的样本数**而不是 `window`

    窗口头部有一段 `j - lookback < 0`，那些位置没有斜率。把它们当「不同向」计入
    分母会系统性地压低早期的一致性，让刚上线那段日子凭空显得反复无常。
    """
    if window < 2 or index < window - 1:
        return None
    current = ema_fast[index]
    base = ema_fast[index - lookback] if index - lookback >= 0 else np.nan
    if not (np.isfinite(current) and np.isfinite(base)):
        return None
    want = 1.0 if current > base else -1.0

    start = index - window + 1
    same = 0
    total = 0
    for j in range(start, index + 1):
        k = j - lookback
        if k < 0:
            continue
        fast_now = ema_fast[j]
        fast_then = ema_fast[k]
        if not (np.isfinite(fast_now) and np.isfinite(fast_then)):
            continue
        total += 1
        if (1.0 if fast_now > fast_then else -1.0) == want:
            same += 1
    if total == 0:
        return None
    return same / total


def label_series(
    candles: Sequence[Mapping[str, Any]],
    params: JudgementConfig = JUDGEMENT,
) -> list[DayLabel]:
    """逐日判定基础阶段。输出与输入**等长同序**，一天一条。

    这是「算数 + 定档」两段的串联：`metrics_series` 出中间量，`_classify` 出档位。
    本函数保留为对外唯一入口——实时判定（`latest_label`）与历史标注（切片）都走它，
    因此量化口径仍然只有一处实现。

    只使用 `candles[i]` 及其之前的数据——没有任何前视。这是历史标注能拿去切片的前提：
    若某天的标签用到了它之后的价格，切片就会「预知」那段行情。

    判定的三个条件全在**收盘后**才成立（ATR/EMA 都用到当日 close），与「北京 08:00 出
    结论、次日 08:00 生效」的时序自洽：D-1 那根日线在 UTC 00:00 收盘，判定跑在 UTC 00:00。

    Args:
        candles: 按时间**升序**排列的日线，每行需含 `date`/`high`/`low`/`close`。
        params: 判定参数；默认取统一配置面的当前值。

    Returns:
        与 `candles` 等长的 `DayLabel` 列表。数据不足以判定的日子 `regime is None`。
    """
    return [classify_day(m, params) for m in metrics_series(candles, params)]


def classify_day(metrics: DayMetrics, params: JudgementConfig = JUDGEMENT) -> DayLabel:
    """把一天的中间量定成档位，连同那批中间量一起返回。

    定不出来时 `regime is None`，**绝不退化成某个档位**——说不出来就说说不出来。这里的
    判断刻意放在分类侧而不是算数侧：中间量只回答「这个数是多少」，「够不够下结论」是
    分类器的事，两个维度可以有不同的门槛。

    ## 三个就绪条件里，有一个是**预热门槛**而不是判据依赖

    #65 把保命档的判据从「ATR% 分位」换成了事件证据，于是 `atr_pct_rank` 不再是任何
    分类器的输入。但它**仍然**留在就绪条件里，这是刻意的：

    - 它是这套判定的**预热门槛**：分位窗口（默认 250 天）攒满，才说明这段历史足够长、
      指标成熟，可以开始对外发布结论。这条政策与「哪个分类器需要哪个数」是两件事——
      后者随时会变（#64 加了两条通道、#65 换掉一条判据），前者不该跟着晃。
    - 拿掉它会让标注序列从第 64 天左右就开始出结论，**而这不是本票要改的东西**。
      `JudgementConfig.warmup_days`（= 分位窗口）与切片预取天数都建立在它上面；静默
      提前 186 天开始判，会让「同一段历史重算」产出与上一版不可比的结果。

    另两个（斜率 / 间距）是真正的**判据依赖**：没有它们连「往哪走」都无从谈起。四档的
    `_classify` 自己会再验一遍它需要的那两项，缺了就抛。
    """
    ready = (
        metrics.atr_pct_rank is not None
        and metrics.ema_slope is not None
        and metrics.separation is not None
    )
    regime = _classify(metrics, params) if ready else None

    # 两维（#64 / #65）**都不再由四档投影**，各自有独立判据：
    #
    # - `cycle`：两条独立路径 + 显式横盘，判不出来落 `unclear`（#64）。
    # - `volatility`：事件驱动代理，跳空 / 放量 / ATR 骤升 / 单日大幅任一成立即
    #   `event_spike`（#65）。
    #
    # `project_to_axes` 仍在，但只服务于兼容读面与历史回填——**写入路径不再调它**。
    # 拿投影去顶替任何一维，都会把这一版刚结束的那个缺陷悄悄装回去：投影出来的
    # `volatility` 是 `high_vol` 这一档的影子（分位配额，必然 20%），而真正的
    # 事件日与「ATR% 分位刚好靠前」是两件事。
    #
    # `regime is None` 的日子两维也必须是 `None`：判不出来就是判不出来。给一个
    # `sideways + normal` 等于拿安静的假结论去喂切片。
    cycle = _classify_cycle(metrics, params) if ready else None
    volatility = _classify_volatility(metrics, params) if ready else None
    return DayLabel(
        date=metrics.date,
        regime=regime,
        metrics=metrics,
        cycle=cycle,
        volatility=volatility,
        atr=metrics.atr,
        atr_pct=metrics.atr_pct,
        atr_pct_rank=metrics.atr_pct_rank,
        quantile_sample=metrics.quantile_sample,
        ema_fast=metrics.ema_fast,
        ema_slow=metrics.ema_slow,
        ema_slope=metrics.ema_slope,
        separation=metrics.separation,
        slope_strength=metrics.slope_strength,
        slope_consistency=metrics.slope_consistency,
        move=metrics.move,
    )


def _classify_cycle(metrics: DayMetrics, params: JudgementConfig) -> Cycle:
    """周期维的**真判定**（#64）：两条独立通道 + 显式横盘，判不出来就是 `unclear`。

    ## 它解决的两个已量化缺陷

    1. **箱体当兜底吞掉缓慢趋势。** 旧结构里 `RANGE` 是「其余」——既非高波动、也无
       趋势就落它。2254 个可比日里人工标「下行」而算法判「箱体」的有 207 天，其中
       125 天（60%）EMA 斜率方向其实是对的，只是 `|separation| <= 0.5` 卡住。
    2. **间距阈值是唯一判据。** 缓慢阴跌时 EMA20 与 EMA60 靠得近，间距到不了半个 ATR。

    ## 趋势的两条独立路径

    - **路径一（间距）**：`slope` 与 `separation` 同向，且 `|separation| > sep_min`。
      这是旧结构那一支，但门槛从 0.5 松到 0.15——因为下面那个显式横盘会承担
      「松下来的部分」：间距不足**且**强度不足才算横盘。
    - **路径二（强度 × 一致性）**：`|slope| / ATR >= strength_min` **且**回看窗口内
      斜率同向占比 `>= consistency_min`。缓慢趋势可能间距不够，但它走得稳、方向一致，
      这一支就是为它开的。

    ## 横盘是**显式证明**，不是兜底

    必须同时满足「无间距」与「无强度」才判 `sideways`；只有一条不够就落 `unclear`。
    这条区分是本函数的全部要点：旧结构把「证明不了是趋势」直接当成「就是横盘」，
    而两者是不同的事实——前者是判不出来，后者是判出来了。混同的代价已经量过：
    207 天系统性错向。

    ## 本函数只产出四个取值，**不返回 `None`**

    `None` 是 `classify_day` 在三个就绪条件缺一时给的（与 `regime is None` 同进退），
    语义是「这一行的周期维**不存在**」。本函数的语义是「就绪了、判了」——
    哪怕是「判不出来」，那也是 `Cycle.UNCLEAR` 这个取值，而不是 `None`。

    这两件事必须能分开：上游要区分「今天没有周期结论」与「今天有结论，结论是判不出来」。
    本函数不替调用方折叠，也不越权替它判断数据够不够。

    ## 只吃中间量，不碰 K 线

    与 `_classify` 同一纪律：规则可以脱离行情直接被测。
    """
    slope = metrics.ema_slope
    separation = metrics.separation
    if slope is None or separation is None:
        raise ValueError(
            "周期维的中间量不全（需要 ema_slope / separation），"
            f"当前 {metrics.date}: slope={slope}, separation={separation}"
        )

    strong_direction = (
        metrics.slope_strength is not None
        and metrics.slope_strength >= params.cycle_strength_min
        and metrics.slope_consistency is not None
        and metrics.slope_consistency >= params.cycle_consistency_min
    )
    sep_min = params.cycle_separation_min_atr

    if slope > 0 and (separation > sep_min or strong_direction):
        return Cycle.BULL
    if slope < 0 and (separation < -sep_min or strong_direction):
        return Cycle.BEAR

    # 补充说明路径二**不校验 separation 与 slope 同向**（这是刻意的，不是遗漏）：
    # 它问的是「快线自己在这段窗口里是否持续地朝一个方向走」，而 separation 是两条均线
    # 的**相对位置**，回答「拉没拉开」。刚拐头的行情正是两者打架的时候——那时斜率已经
    # 转向、间距还在旧方向——按斜率定向是对的（ADR 实验 #3「背离按斜率定向」比弃权
    # 多 4.6 pt 一致率）。要求两者同向会把这类刚启动的趋势推回 unclear。
    #
    # 走到这里说明两条路径都没走通。**只有「间距不足」与「强度不足」同时成立**
    # 才是横盘；任何一边够（甚至只是强度够而一致性不够）都不是横盘，是判不出来。
    weak_strength = (
        metrics.slope_strength is None
        or metrics.slope_strength < params.cycle_strength_min
    )
    if abs(separation) < sep_min and weak_strength:
        return Cycle.SIDEWAYS
    return Cycle.UNCLEAR


def _classify_volatility(metrics: DayMetrics, params: JudgementConfig) -> Volatility:
    """波动维的**真判定**（#65）：事件驱动代理，四条独立证据任一成立即 `EVENT_SPIKE`。

    ## 为什么不再按分位判

    旧的 `rank > high_vol_quantile` 在数学上锁死「250 日窗口里必有 20% 的日子判高波动」
    ——实测 434 天（19.3%），而人工真值里高波动是 **0 天**。按配额判出来的东西不是
    「观察到的波动」，是窗口的算术必然：它无法随行情自然变化，因此也**无法被证伪**
    ——你永远无法用「今年高波动日子太少」去反驳它，它每年都会给你 20%。

    ## 四条证据（或，不是与）

    - **跳空** `|open_t / close_{t-1} - 1|` > `gap_ratio`：隔夜信息一次性重定价。
    - **放量** `volume_t / MA20(volume)` > `volume_multiple`：参与度异常放大。
    - **ATR 骤升** `ATR_t / ATR_{t-1}` > `atr_jump_ratio`：波动率水平本身跳变。
    - **单日大幅** `|close_t / close_{t-1} - 1|` > `move_ratio`：当天就走完了。

    任何一条单独成立就够：一次 10% 的单日下跌本身就是事件，要求「同时又放量、
    又跳空」等于把识别窗口缩到少数几天——而那几天恰恰是最该被认出来的。

    ## 代理的假阳性必须可追溯

    消息面数据源当前没有，所以这四条是**价格代理**：它会把「没有新闻的大波动」也
    判成事件波动。这一点不在这里掩饰，而是由写入路径落 `volatility_source = "proxy"`
    + 日报显式标注来承担——读日报的人必须能分清「这一格是判出来的代理」与「这一格是
    消息面确认过的」。

    ## 只吃中间量，不碰 K 线

    与另两个分类器同一纪律：规则可以脱离行情直接被测。四个量全为 `None` 时**不是**
    事件波动——「没有量能数据」与「量能平静」是两件事，编一个 1.0 会让它们长得一样。
    这里的选择是「证据不足 ⇒ normal」，与 `_classify_cycle` 证据不足 ⇒ `unclear` 的
    取向不同，理由是不对称的：`event_spike` 会**停掉全场**，缺证据时朝「不停」倒才对；
    而 `cycle` 判不出来没有这种代价，所以它如实说 `unclear`。
    """
    if metrics.move is not None and metrics.move > params.move_ratio:
        return Volatility.EVENT_SPIKE
    if metrics.gap is not None and metrics.gap > params.gap_ratio:
        return Volatility.EVENT_SPIKE
    if (
        metrics.volume_ratio is not None
        and metrics.volume_ratio > params.volume_multiple
    ):
        return Volatility.EVENT_SPIKE
    if metrics.atr_jump is not None and metrics.atr_jump > params.atr_jump_ratio:
        return Volatility.EVENT_SPIKE
    return Volatility.NORMAL


def _classify(metrics: DayMetrics, params: JudgementConfig) -> BaseRegime:
    """按固定优先级定档。顺序即 `PRIORITY`：先问「该不该保命」，再问「往哪走」。

    只吃中间量结构体，不碰 K 线：这是 prefactor 的核心——算数与定档分开之后，加第二个
    维度的分类器不必复制一遍算数。

    调用前提是三个条件都齐（`classify_day` 已验）。半截数据在这里**抛**而不是退回箱体
    震荡：箱体震荡是兜底档，拿它去接「算不出来」正是本模块反复在防的那类失败——一条
    安静的假标签，切片会当真。
    """
    slope = metrics.ema_slope
    separation = metrics.separation
    if slope is None or separation is None:
        raise ValueError(
            "中间量不足以定档（需要 ema_slope / separation），"
            f"当前 {metrics.date}: slope={slope}, separation={separation}"
        )
    # **保命档的判据自 #65 起改源**：不再是「ATR% 分位 > 80%」，而是与波动维同一个
    # 事件驱动代理。四档是兼容层，它必须说与 volatile 维同一件事——两处判据各写一份
    # 的话，同一条记录里 `regime=high_vol` 而 `volatility=normal` 会天天出现，而两边
    # 各自看都自洽。这里直接调那个分类器，不重写阈值。
    if _classify_volatility(metrics, params) is Volatility.EVENT_SPIKE:
        return BaseRegime.HIGH_VOL
    threshold = params.trend_separation_min_atr
    # 符号与间距**同时**指向同一方向才算趋势：只看向间距会让 (EMA20−EMA60) 为负的
    # 那段被读成一种趋势，而它到底是「下行」还是「刚拐头」需要斜率来分辨。
    if slope > 0 and separation > threshold:
        return BaseRegime.UPTREND
    if slope < 0 and separation < -threshold:
        return BaseRegime.DOWNTREND
    return BaseRegime.RANGE


def latest_label(
    candles: Sequence[Mapping[str, Any]],
    params: JudgementConfig = JUDGEMENT,
) -> DayLabel | None:
    """最新一天的基础阶段；输入为空时 `None`。

    单元 4 走这一条，**不要自己写「只算最后一天」的版本**：那样量化口径就有了第二处实现，
    而它与历史标注（切片依据）之间的任何一点差异都会静默存在——两边的结论都看着很正常。
    """
    series = label_series(candles, params)
    return series[-1] if series else None
