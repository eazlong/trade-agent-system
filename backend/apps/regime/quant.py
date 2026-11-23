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

## 四档与优先级

高波动 > 下行趋势 > 上行趋势 > 箱体震荡，互斥且固定（`PRIORITY`）。**箱体震荡是兜底**，
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

#: 判定优先级，高到低；`RANGE` 是兜底因而居末。改这个顺序等于改机制语义，不要顺手动。
PRIORITY: tuple[BaseRegime, ...] = (
    BaseRegime.HIGH_VOL,
    BaseRegime.DOWNTREND,
    BaseRegime.UPTREND,
    BaseRegime.RANGE,
)


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
    return DayLabel(
        date=metrics.date,
        regime=_classify(metrics, params) if ready else None,
        metrics=metrics,
        atr=metrics.atr,
        atr_pct=metrics.atr_pct,
        atr_pct_rank=metrics.atr_pct_rank,
        quantile_sample=metrics.quantile_sample,
        ema_fast=metrics.ema_fast,
        ema_slow=metrics.ema_slow,
        ema_slope=metrics.ema_slope,
        separation=metrics.separation,
    )


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
