"""行情阶段机制的**唯一配置面**（v1 只读）。

CONTEXT.md 决策：所有机制阈值收敛到同一处，不散落为各模块常量——它们会被一起
调整、一起审计，散开后「为什么这个门槛是 30」将无处可查。

三条纪律：

1. **只读**。v1 没有任何权限设施（无角色模型），「改动广播给所有人」只能告知、
   不能阻止；改阈值必须改代码发版。等有了角色模型再开放写入，届时留痕机制已就位。
   所以这里不放 DB 模型，也不提供 setter；每个分组都是 frozen dataclass。

2. **留痕靠快照，不靠读回配置**。判定记录内嵌「当时生效的完整参数快照」：半年后
   要解释「这条结论用的是哪套参数」时，答案在记录自己身上，不必翻 git 考古。
   为此每个分组都必须是 `dataclasses.replace` 可派生的 frozen dataclass，并且
   能经 `snapshot()` 变成 JSON-safe 的字面量。**测试就是把这两条钉住**。

3. **自校验**。数值边界在 import 时检查（`__post_init__`），坏编辑在进程启动时
   就炸，而不是先安静地跑出一批奇怪判定、等有人去翻日报才发现。

分组按「会被一起调整」划分，不按使用它的模块划分。

## 分组清册

CONTEXT.md 的阈值全枚举，以及每一组落在哪一段（防止「没写 = 忘了」）：

| 分组 | 内容 | 状态 |
|------|------|------|
| `CANDLES` | 日线拉取与落库参数 | ✔ 单元 1 |
| `JUDGEMENT` | 判定指标周期、分位阈值、趋势判据 | ✔ 单元 3 |
| `JUDGEMENT_LIFECYCLE` | 最小持续期、状态过期 | ✔ 本单元 |
| `EVIDENCE` | 切片证据门槛 | ✔ 本单元 |
| `BOX` | 箱体判定与高低点结构的参数（约 10 个） | 单元 8（日报要写依据时才定形状） |
| `NEWS` | 采集窗口、预筛条数、正文截断、白名单源 | 单元 5 |
| `SHADOW` | Shadow 到期上下限、一致率达标阈值 | 单元 8 |
| `REPORT` | 日报投递看门狗时刻 | 单元 8 |
| `EVENT` | 事件窗口与全局上下限、事件库衰减告警、候选失效期 | 第②段 |
| `DERISK` | 滑点上限、减仓分片数上限 | 第②段 |
| `SELF_FUSE` | 自熔断频率 | 第②段 |
| `RECOVERY` | 恢复豁免期 | 第③段 |

后四组的数值 CONTEXT.md 已给出，但**形状还不可知**（例如事件窗口是「按事件类型的
一张表」还是「一个默认对」，取决于事件库本身长什么样）。先写下来等于替将来的设计
拍一个形状，而那个形状会被后来者 cargo-cult。所以它们等各自的段落地时再加，
不在此处预置空壳。

`BOX` 是同一个理由的另一种形态：形状是已知的（`detect_box_range` 的入参就摆在那里），
但**约 10 个数值没有一个是现在能给出理由的**，而且**基础阶段不依赖它**——「箱体震荡」
是四档里的兜底，检不检出箱体都落到它。所以箱体与高低点结构只在日报要写依据时才需要，
届时按日报真正要回答的问题去定参数，而不是现在照着函数签名誊一遍。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------- #
# 分组定义
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CandleConfig:
    """日线数据的拉取与落库参数。

    这些是**数据面的操作参数**，不是机制阈值（不决定任何判定结果），但仍放在
    这里：第①段的每个旋钮都只有一个家，读者不必猜「还有没有别处能调」。
    """

    # 单一判定源：机制是全市场级，BTC 日线是历史标注与每日判定的同一份真相。
    symbol: str = "BTC/USDT"
    exchange: str = "binance"
    # == apps.datasource.base.KlineInterval.D1，此处写字面量以免为了一根字符串
    # 把 datasource 的模块图拉进本 app。一致性由测试对 KlineInterval.D1.value 断言。
    timeframe: str = "1d"

    # ccxt 单次请求上限。分页游标推进（而非固定窗口）由调用方保证。
    page_limit: int = 1000

    # 「已收盘」的宽容带：判定任务排在 UTC 00:00 整（北京 08:00），而那一刻
    # 前一日线刚收盘，时钟微偏就会让游标永远落后一天。留一点余量让它仍算收盘，
    # 落库是幂等 upsert，值若尚未最终化，下一次运行会自愈。
    close_tolerance: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.page_limit < 1:
            raise ValueError(f"page_limit 必须 >= 1，当前 {self.page_limit}")
        if self.timeframe != "1d":
            # 判定与切片的自然日口径建立在「日线」上：D 的 date 就是它的开盘日，
            # 且业务日 == UTC 开盘日 == 北京同日。换成别的周期，这个等式不成立。
            raise ValueError(
                f"timeframe 必须为 '1d'——判定与切片的日粒度口径依赖它，当前 {self.timeframe!r}"
            )


@dataclass(frozen=True)
class JudgementConfig:
    """量化判定的指标周期与分位阈值。

    阈值一律用**滚动历史分位数**表达而非绝对水平：币圈波动率水平会整体漂移，
    固定常量必然周期性失效。分位窗口因此是这套参数里最重要的一个数——它决定了
    「高波动」是相对什么而言的高波动。
    """

    # 指标周期（CONTEXT.md：ATR 默认 14 日线、EMA 默认 20/60）
    atr_period: int = 14
    ema_fast_period: int = 20
    ema_slow_period: int = 60

    # 滚动分位窗口：250 个自然日 ≈ 一年，够长到包含一轮完整的波动收敛与扩张。
    quantile_window_days: int = 250

    # 高波动档：ATR% 分位 > 80%。四档里唯一由单一阈值直接给出的档位。
    high_vol_quantile: float = 0.80

    # 趋势判据 = 「EMA 斜率符号」与「(EMA20−EMA60)/ATR 过阈值」**同时**成立。
    # 两者分工不同：符号是廉价的粗筛（噪声大但不会漏方向），间距倍数才是实质门槛。
    # 只要符号、不要间距幅度，噪声喊一声就算趋势；只要间距、不要符号，(EMA20−EMA60)
    # 是带符号的，负间距会变成「下行」——符号那一项其实在防的就是这个误读。
    #
    # 斜率回看 5 个自然日：短于「最小持续期 5 天」的斜率是在测噪声（机制自己都规定
    # 阶段至少站 5 天才算数），长于它的斜率则追不上已经在走的趋势。
    ema_slope_lookback_days: int = 5

    # 快慢均线间距至少要走满 0.5 个 ATR。用 ATR 归一化而非绝对价格：币圈价格量级
    # 跨四年变化几十倍，绝对间距门槛必然周期性失效（与「阈值按分位自校准」同一理由）。
    trend_separation_min_atr: float = 0.5

    @property
    def warmup_days(self) -> int:
        """历史标注要在回测窗口起点前多取多少天。

        等于分位窗口本身：要算出第一个分位数，就得先攒满一整个窗口。**刻意做成
        派生属性而不是第二个字段**——两个独立字段可以互相漂移（有人把窗口改成
        500 却忘了改预热），而漂移的表现是标注序列开头一段 NaN，看起来像「那几天
        数据缺失」，不像参数配错。
        """
        return self.quantile_window_days

    def __post_init__(self) -> None:
        for name in ("atr_period", "ema_fast_period", "ema_slow_period"):
            if getattr(self, name) < 2:
                raise ValueError(f"{name} 必须 >= 2，当前 {getattr(self, name)}")
        if self.ema_fast_period >= self.ema_slow_period:
            raise ValueError(
                f"ema_fast_period({self.ema_fast_period}) 必须小于 "
                f"ema_slow_period({self.ema_slow_period})"
            )
        if self.quantile_window_days < 2:
            raise ValueError(
                f"quantile_window_days 必须 >= 2，当前 {self.quantile_window_days}"
            )
        if not 0.0 < self.high_vol_quantile < 1.0:
            raise ValueError(
                f"high_vol_quantile 必须在开区间 (0, 1) 内，当前 {self.high_vol_quantile}"
            )
        if self.ema_slope_lookback_days < 1:
            raise ValueError(
                f"ema_slope_lookback_days 必须 >= 1，当前 {self.ema_slope_lookback_days}"
            )
        if self.trend_separation_min_atr <= 0:
            raise ValueError(
                f"trend_separation_min_atr 必须为正，当前 {self.trend_separation_min_atr}"
            )


@dataclass(frozen=True)
class JudgementLifecycleConfig:
    """判定结论的时间约束。

    两个数管的是两件不同的事，但都在回答「上一版结论还算不算数」，会被一起调整，
    所以同组：

    - `min_dwell_days`：阶段是慢变量，切换后至少站住 5 个自然日才允许再切。
      挡的是判定在阈值附近来回抖（每天都换个说法，日报就没法读）。
    - `stale_after_days`：状态过期超 3 个自然日就不再新增停用决策。判定任务故障
      时**保持上一有效状态**（绝不降级成「最保守」——那会让任务故障伪装成风控
      动作），但一个过期太久的结论不该继续指挥停用。
    """

    min_dwell_days: int = 5
    stale_after_days: int = 3

    def __post_init__(self) -> None:
        for name in ("min_dwell_days", "stale_after_days"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负，当前 {getattr(self, name)}")


@dataclass(frozen=True)
class EvidenceConfig:
    """切片结论的证据门槛。

    门槛是「用十几笔交易停掉一个策略」的唯一天然刹车，因此不足者判为「中性」，
    而**中性不构成停用理由**——宁可如实显示中性，也不为了机制看起来有输出而降门槛。

    `min_trades` 是基数；稀有阶段按该阶段占天数的比例缩放到 `min_trades` 以下，
    但缩放后仍必须同时满足 `min_months`，并且必须同时输出置信区间。
    """

    min_trades: int = 30
    min_months: int = 3

    def __post_init__(self) -> None:
        if self.min_trades < 1:
            raise ValueError(f"min_trades 必须 >= 1，当前 {self.min_trades}")
        if self.min_months < 1:
            raise ValueError(f"min_months 必须 >= 1，当前 {self.min_months}")


CANDLES = CandleConfig()
JUDGEMENT = JudgementConfig()
JUDGEMENT_LIFECYCLE = JudgementLifecycleConfig()
EVIDENCE = EvidenceConfig()


# --------------------------------------------------------------------------- #
# 参数快照
# --------------------------------------------------------------------------- #

# 快照可用的分组及其键名。判定/切片记录里嵌的键就是这里的字符串，所以它们是
# **落库契约**：改名等于让新旧记录出现两套键，不要轻易动。
GROUPS: dict[str, Any] = {
    "candles": CANDLES,
    "judgement": JUDGEMENT,
    "judgement_lifecycle": JUDGEMENT_LIFECYCLE,
    "evidence": EVIDENCE,
}


def _jsonable(value: Any) -> Any:
    """把配置值转成 JSONField 能直接存的东西。

    三条转换规则各自有理由：

    - `Decimal` → `str`：金额口径恒用 Decimal，转 float 会在快照这一步丢精度，
      而快照是事后归因的唯一依据。字符串能无损还原。
    - `timedelta` → 秒（float）：可比较、可计算，比 "PT5M" 之类更好用。
    - `Enum` → `value`：存的是枚举的实际取值，不是成员名——成员名会因为重构而变。
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def snapshot(*names: str) -> dict[str, dict]:
    """取若干分组当前值的 JSON-safe 快照，供判定/切片记录内嵌。

    只接受已登记的分组名，且**拼错就报错**：若允许「取不到就跳过」，一次手滑的
    快照会安静地少一组参数，而少了的那组恰恰是日后要用来解释结论的那组。
    """
    unknown = [n for n in names if n not in GROUPS]
    if unknown:
        raise KeyError(f"未登记的配置分组：{unknown}；可用：{sorted(GROUPS)}")

    result: dict[str, dict] = {}
    for name in names:
        group = GROUPS[name]
        if not is_dataclass(group) or not isinstance(group, type(group)):
            result[name] = {"value": _jsonable(group)}
            continue
        result[name] = {k: _jsonable(v) for k, v in asdict(group).items()}
        # 派生属性不在 asdict 里，但它同样是「当时生效的参数」（判定与历史标注都
        # 依赖预热长度），所以显式补进去。
        for extra in ("warmup_days",):
            if isinstance(getattr(group, extra, None), int) and extra not in result[name]:
                result[name][extra] = getattr(group, extra)
    return result


# 全部参数组的合并快照：判定记录用这个，省得逐次点名。
def full_snapshot() -> dict[str, dict]:
    """所有已登记分组的快照。判定记录直接内嵌它。"""
    return snapshot(*GROUPS)


def dumps(*names: str) -> str:
    """快照的 JSON 字面量（排序键，便于比对两条记录是否同参）。"""
    return json.dumps(snapshot(*names) if names else full_snapshot(),
                      ensure_ascii=False, sort_keys=True)
