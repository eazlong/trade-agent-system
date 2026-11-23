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
PRIORITY: tuple[BaseRegime, ...] = (
    BaseRegime.HIGH_VOL,
    BaseRegime.DOWNTREND,
    BaseRegime.UPTREND,
    BaseRegime.RANGE,
)


class Cycle(str, Enum):
    """**周期维**（慢变量）：往哪走。

    与 `BaseRegime` 的关系是「新结构 / 旧兼容层」：本版仍由 `BaseRegime` 投影出这一维
    （见 `project_to_axes`），所以四个取值里这一版只会出现三个——`UNCLEAR` 要等
    #64 把横盘改成显式判定之后才会产出。先把它列进来，是因为枚举一旦落库就是契约，
    取值集合必须一次定完；事后补一个取值等于一次数据迁移。
    """

    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    UNCLEAR = "unclear"

    @property
    def display(self) -> str:
        """展示层的中文名。与 `BaseRegime.display` 同一约定：取值不动中文。"""
        return CYCLE_DISPLAY[self.value]


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
#: - `compatible_projection`：本版新写入的行。两维目前仍由四档投影而来，所以它与历史行
#:   **同样是投影**，只是发生在写入时而不是迁移时。分开标是为了让「那天的高波动是判出来
#:   的还是映射来的」答得上——#65 引入事件代理之后会写 `proxy`，届时三者必须可区分。
VOLATILITY_SOURCE_LEGACY = "legacy_mapping"
VOLATILITY_SOURCE_PROJECTION = "compatible_projection"

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
    """`(cycle, volatility)` → 四档。**还原不出来的组合必须抛**，不许猜。

    这是反向投影，唯一用途是把两维读回本版既有的四档世界（读面兼容、历史回填校验）。
    它有两处「还原不了」，各自抛而不是回落到某档：

    - `cycle is None`（且有波动结论）：这正是 `high_vol` 的形状，还原得出来。
      但 `cycle is None` 配 `normal` 还原不出来——没有方向、也没有事件波动，
      旧四档里没有这一格。
    - `bull`/`bear`/`sideways` 配 `event_spike`：旧四档表达不了「有方向又高波动」，
      只能退回 `high_vol` 而丢掉方向。那正是这次要结束的状态，静默丢一维比抛更坏。

    `UNCLEAR`（判不出来）还原成 `None`：两维都说了「说不出来」，与未判定同形。
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
    if cycle is Cycle.UNCLEAR:
        if volatility is Volatility.EVENT_SPIKE:
            # 判不出方向 + 事件波动：旧结构里这天就是 high_vol。
            return BaseRegime.HIGH_VOL
        return None
    if volatility is Volatility.EVENT_SPIKE:
        raise ValueError(
            f"{cycle.value} + event_spike 还原不了四档：旧结构表达不了「有方向又高波动」，"
            "静默丢掉方向比报错更坏"
        )
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

    # 波动代理（事件驱动判定用；本阶段不参与定档）
    gap: float | None
    volume_ratio: float | None
    atr_jump: float | None


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

    #: 两维（#63）。本版由 `regime` 投影而来，不是第二套判据——见 `project_to_axes`。
    #: `cycle` 在 `high_vol` 那天是 `None`（旧结构里波动吃掉了周期，如实留空）。
    #: 未判定日两维**同时**是 `None`。
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

    定不出来（三个条件缺一）时 `regime is None`，**绝不退化成某个档位**——说不出来
    就说说不出来。这里的判断刻意放在分类侧而不是算数侧：中间量只回答「这个数是多少」，
    「够不够下结论」是分类器的事，两个维度可以有不同的门槛。
    """
    ready = (
        metrics.atr_pct_rank is not None
        and metrics.ema_slope is not None
        and metrics.separation is not None
    )
    regime = _classify(metrics, params) if ready else None

    # 周期维（#64）**不再由四档投影**：它有了自己的判据（两条独立路径 + 显式横盘），
    # 投影只在 `volatility` 那一条上还成立。这条分开是本票的全部内容——只要 cycle
    # 还是 `project_to_axes` 的产物，`sideways` 就永远是「其余」，缓慢阴跌必然落进箱体。
    #
    # `regime is None` 的日子 cycle 也必须是 `None`：判不出来就是判不出来，给一个
    # `sideways` 等于拿安静的假结论去喂切片。
    cycle = _classify_cycle(metrics, params) if ready else None
    volatility = project_to_axes(regime)[1] if regime is not None else None
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
    )


def _classify_cycle(metrics: DayMetrics, params: JudgementConfig) -> Cycle | None:
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

    ## 返回 `None` 而不是 `Cycle.UNCLEAR`

    `None` = 这一行的周期维**不存在**（`classify_day` 在三个就绪条件缺一时的情形，
    与 `regime is None` 同进退）。`unclear` = 就绪了、判了、但方向证明不出来。
    上游要能分开这两件事，所以这里不替调用方折叠。

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

    # 走到这里说明两条路径都没走通。**只有「间距不足」与「强度不足」同时成立**
    # 才是横盘；任何一边够（甚至只是强度够而一致性不够）都不是横盘，是判不出来。
    weak_strength = (
        metrics.slope_strength is None
        or metrics.slope_strength < params.cycle_strength_min
    )
    if abs(separation) < sep_min and weak_strength:
        return Cycle.SIDEWAYS
    return Cycle.UNCLEAR


def _classify(metrics: DayMetrics, params: JudgementConfig) -> BaseRegime:
    """按固定优先级定档。顺序即 `PRIORITY`：先问「该不该保命」，再问「往哪走」。

    只吃中间量结构体，不碰 K 线：这是 prefactor 的核心——算数与定档分开之后，加第二个
    维度的分类器不必复制一遍算数。

    调用前提是三个条件都齐（`classify_day` 已验）。半截数据在这里**抛**而不是退回箱体
    震荡：箱体震荡是兜底档，拿它去接「算不出来」正是本模块反复在防的那类失败——一条
    安静的假标签，切片会当真。
    """
    rank = metrics.atr_pct_rank
    slope = metrics.ema_slope
    separation = metrics.separation
    if rank is None or slope is None or separation is None:
        raise ValueError(
            "中间量不足以定档（需要 atr_pct_rank / ema_slope / separation），"
            f"当前 {metrics.date}: rank={rank}, slope={slope}, separation={separation}"
        )
    if rank > params.high_vol_quantile:
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
