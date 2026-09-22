"""行情阶段机制的**唯一配置面**（v1 只读）。

CONTEXT.md 决策：所有机制阈值收敛到同一处，不散落为各模块常量——它们会被一起
调整、一起审计，散开后「为什么这个门槛是 30」将无处可查。

两条纪律：

1. **只读**。v1 没有任何权限设施（无角色模型），「改动广播给所有人」只能告知、
   不能阻止；改阈值必须改代码发版。等有了角色模型再开放写入，届时留痕机制已就位。
   所以这里不放 DB 模型，也不提供 setter。

2. **留痕靠快照，不靠读回配置**。判定记录内嵌「当时生效的完整参数快照」：半年后
   要解释「这条结论用的是哪套参数」时，答案在记录自己身上，不必翻 git 考古。
   为此每个分组都是 frozen dataclass，必须能用 `dataclasses.replace` 派生副本
   （测试也靠它临时改参数，不必改全局）。

分组按「会被一起调整」划分，不按使用它的模块划分。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


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
    # 把 datasource 的模块图拉进本 app。
    timeframe: str = "1d"

    # ccxt 单次请求上限。分页游标推进（而非固定窗口）由调用方保证。
    page_limit: int = 1000

    # 「已收盘」的宽容带：判定任务排在 UTC 00:00 整（北京 08:00），而那一刻
    # 前一日线刚收盘，时钟微偏就会让游标永远落后一天。留一点余量让它仍算收盘，
    # 落库是幂等 upsert，值若尚未最终化，下一次运行会自愈。
    close_tolerance: timedelta = timedelta(minutes=5)


CANDLES = CandleConfig()
