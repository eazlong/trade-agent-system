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
| `DEACTIVATION` | 人工豁免生效期 | ✔ 单元 7 |
| `BOX` | 箱体判定与高低点结构的参数（约 10 个） | 单元 8（日报要写依据时才定形状） |
| `NEWS` | 采集窗口、预筛条数、正文截断、白名单源 | ✔ 本单元 |
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

## `NEWS` 的白名单源：可达性取决于代理，第一类因此换了形态

`NewsConfig.sources` 的默认值是**实机验证过可达**的那批源（2026-09-22 从本项目的运行
环境逐个探测，`httpx` + `follow_redirects`）。探测结果**分两种情形**，而这两种情形的差别
不是细节，它决定了第一类源能不能有成员：

- **不走代理**：`cointelegraph.com/rss`、`theblock.co/rss.xml`、`decrypt.co/feed`、
  `federalreserve.gov/feeds/press_all.xml`、`ecb.europa.eu/rss/press.html` 可达；
  `binance.com`、`okx.com`、`blog.kraken.com`、`blog.coinbase.com`、`blog.bitmex.com`、
  `bitget.com` 一律连接超时（`bls.gov` 是 403）。
- **走 `settings.WEB_PROXY`**：上述源照常可达，**并且交易所也通了**。本部署的容器里
  始终配着这个代理（`web_fetch.py` 走的就是它），所以运行时走的是这一支——但这条
  依赖必须显式写出来，否则「白名单里有没有交易所」会被一个没写下来的环境前提决定。

通了之后发现交易所这一类的**形态与前两类不同**：Kraken/Coinbase 那几家仍不可达，而
Binance 与 OKX 各自暴露了一个结构化、免鉴权的**公告 JSON 接口**（比公告页更适合当输入）：

- `binance.com/bapi/composite/v1/public/cms/article/list/query?...&catalogId=48`（公告目录）
- `okx.com/api/v5/support/announcements`

所以 v1 的三类白名单**都有成员**，但第一类不是 RSS。这件事不能靠「一个通用解析器吃掉
所有源」蒙混过去：RSS/Atom 是标准形态，一套解析入口通吃；而这两个接口的信封各不相同
（`data.catalogs[].articles[]` vs `data[].details[]`，时间戳一个毫秒整数一个字符串，
链接一个要给字段一个要用 code 拼）。**这正是 `NewsSource.parser` 存在的理由**：第二、
三类用通用的 `rss`，第一类用具名 parser，把「这个源要怎么读」变成配置表上一行显式的、
可审计的字面量，而不是藏在采集器里的一个 `if "binance" in url`。

**刻意不含 HTML 公告页。** 探测中 `announcements.bybit.com` 的 HTML 是可达的，但为公告页
写抓取器意味着把页面结构焊进代码，而页面结构没有任何契约保证——站点改版后抓取器**不会
报错，只会返回 0 条**，那是一条比抓取失败更难发现的故障（它长得跟「今天很平静」一模一样）。
真需要时应该加一个具名 parser，而不是加一个「通用 HTML 抓取器」。
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


@dataclass(frozen=True)
class DeactivationConfig:
    """停用推导（第①段单元 7）里唯一一个可调的数。

    `exemption_days` 是人工豁免的生效期（自然日）。CONTEXT.md 把它明确划进「与判定
    参数同一配置面」，所以它在这里而不在豁免模型上：模型上的默认值会让「10 天」出现
    第二份，而两份会在某次有人改了其中一份之后开始分歧——分歧的表现是「豁免到底
    多久」，而它恰好是唯一一个让人跳过自动停用的开关，错了没人会发现。

    **生效期在写入豁免时就换算成绝对时刻存下来**（`expires_at`），所以改动这个数
    **不会**追溯延长或缩短已经发出的豁免。同 `RegimeJudgement` 存 `attribute_date` 的
    理由：留痕靠记录自己，不靠用今天的规则去反推。生效期因此是「发豁免那一刻的配置」，
    而不是「查询那一刻的配置」。
    """

    exemption_days: int = 10

    def __post_init__(self) -> None:
        if self.exemption_days < 1:
            raise ValueError(
                f"exemption_days 必须 >= 1，当前 {self.exemption_days}"
            )


# --------------------------------------------------------------------------- #
# 资讯通道（第①段单元 5）
# --------------------------------------------------------------------------- #


class NewsSourceKind(str, Enum):
    """白名单源的三个类别。

    做成枚举而不是自由字符串，是因为这三个类别在日报与采集结果里要**分别计数**：
    「交易所公告这一类今天哑了」和「加密媒体这一类今天 0 条」是两条不同的信息，
    而只要类别名能拼错，计数就会静默地掉进第四类。
    """

    EXCHANGE = "exchange"
    CRYPTO_MEDIA = "crypto_media"
    MACRO = "macro"

    @property
    def display(self) -> str:
        return _NEWS_KIND_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


#: 类别 → 展示名。落进模型 choices 与日报措辞，不就地写中文。
_NEWS_KIND_DISPLAY = {
    NewsSourceKind.EXCHANGE: "交易所公告",
    NewsSourceKind.CRYPTO_MEDIA: "主流加密媒体",
    NewsSourceKind.MACRO: "宏观类站点",
}


@dataclass(frozen=True)
class NewsSource:
    """白名单里的一个源。

    `name` 是**稳定标识**：资讯条目与判定留痕里记的都是它。改名等于新开一个源——
    历史条目上的来源名不会被追溯改写，那是「记录是化石」的另一面。

    `parser` 指名**解析入口**（取值见 `NEWS_PARSERS`），不是站点名，也不是「这个源属于
    哪一类」——类别是 `kind`，两者正交：`rss` 这一个 parser 同时服务加密媒体与宏观两类。

    **刻意不做成「按站点写抓取器」**：那会把每个站点的页面结构焊进代码里，而页面结构
    是随时会变的。RSS/Atom 是标准形态，一个解析入口通吃任意多家；只有**接口信封各不相同
    的结构化 API**（Binance / OKX 的公告接口）才需要各自的 parser，而那样的 parser 依赖的
    是接口契约（有版本、会报错），不是页面结构（无契约、改版后安静地返回 0 条）。
    两者是「会坏的依赖」与「无声坏的依赖」的区别，所以只接受前者。
    """

    name: str
    url: str
    kind: NewsSourceKind
    parser: str = "rss"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("资讯源的 name 不能为空——它是条目与判定留痕里的来源标识")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"资讯源 {self.name} 的 url 必须是 http(s)，当前 {self.url!r}")
        if self.kind not in NewsSourceKind:
            raise ValueError(
                f"资讯源 {self.name} 的 kind 未登记：{self.kind!r}；"
                f"可用：{[m.value for m in NewsSourceKind]}"
            )
        if self.parser not in NEWS_PARSERS:
            raise ValueError(
                f"资讯源 {self.name} 的 parser 未登记：{self.parser!r}；"
                f"可用：{list(NEWS_PARSERS)}"
                "——拼错的 parser 会让整个源安静地取不到条目，所以在 import 时就拦下"
            )


#: 解析入口的登记表。加一个成员 = 在采集器里加一个同名函数，**两边必须是同一批名字**
#: （由采集器的测试对这里断言）。`rss` 含 Atom——两者解析路径相同。
#:
#: `html` 不在表里，理由见模块 docstring：页面结构无契约，改版后返回 0 条而不是报错。
NEWS_PARSERS = ("rss", "binance_announcements", "okx_announcements")


#: 关键词预筛的默认词表。**刻意放宽**：命中与否只是「要不要花 token 送进 LLM」的
#: 成本闸门，不是相关性判定——漏掉一条该被看到的资讯，代价是一次错误的抬升缺失；
#: 多送一条无关的，代价是几十个 token。两个方向的代价不对称，所以宁滥勿缺。
#: 匹配是**小写子串**匹配，因此像 "ban" 也会命中 "urban" 这类词，这是接受的。
#: 全部小写（匹配前会把标题与正文也小写化）；中文词不受大小写影响，一并列出。
NEWS_KEYWORDS: tuple[str, ...] = (
    # 标的与市场
    "bitcoin", "btc", "crypto", "stablecoin", "tether", "usdt", "etf",
    "liquidation", "liquidations", "exchange", "custody",
    # 监管、法律与安全事件（最可能触发抬升的一类）
    "regulation", "regulator", "lawsuit", "settlement", "ban", "sanction",
    "tariff", "hack", "exploit", "halt", "delist", "bankruptcy",
    # 宏观
    "interest rate", "rate cut", "rate hike", "inflation", "cpi", "fomc",
    "fed", "federal reserve", "recession", "liquidity",
    # 中文（宏观类源里有中文标题的站点）
    "比特币", "加密", "监管", "禁止", "黑客", "被盗", "稳定币",
    "清算", "降息", "加息", "通胀", "美联储", "衰退", "关税", "制裁",
)


#: 实机验证可达的默认源，三类齐全。可达性与「为什么第一类走具名 parser」见模块
#: docstring。补源只需在这里加一行；**这一类今天有几个源**由 `kinds_covered` 回答。
NEWS_SOURCES: tuple[NewsSource, ...] = (
    # ---- 第一类：交易所公告 ------------------------------------------------ #
    # 这两个源只有走 `settings.WEB_PROXY` 才可达（容器里配着，见模块 docstring）。
    # `pageSize=40` 与 `NewsConfig.max_items_per_source` 同值：前者是接口一次给多少，
    # 后者是采集器收多少，配成一样是为了让「接口明明有更多、我们却只看到 40 条」这种
    # 情况不出现——真被 `max_items_per_source` 截断时，那是一次可见的截断。
    NewsSource(
        name="binance",
        url=(
            "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
            "?type=1&pageNo=1&pageSize=40&catalogId=48"
        ),
        kind=NewsSourceKind.EXCHANGE,
        parser="binance_announcements",
    ),
    NewsSource(
        name="okx",
        url="https://www.okx.com/api/v5/support/announcements",
        kind=NewsSourceKind.EXCHANGE,
        parser="okx_announcements",
    ),
    # ---- 第二类：主流加密媒体 ---------------------------------------------- #
    NewsSource(
        name="cointelegraph",
        url="https://cointelegraph.com/rss",
        kind=NewsSourceKind.CRYPTO_MEDIA,
    ),
    NewsSource(
        name="theblock",
        url="https://www.theblock.co/rss.xml",
        kind=NewsSourceKind.CRYPTO_MEDIA,
    ),
    NewsSource(
        name="decrypt",
        url="https://decrypt.co/feed",
        kind=NewsSourceKind.CRYPTO_MEDIA,
    ),
    # ---- 第三类：宏观类站点 ------------------------------------------------ #
    NewsSource(
        name="federalreserve",
        url="https://www.federalreserve.gov/feeds/press_all.xml",
        kind=NewsSourceKind.MACRO,
    ),
    NewsSource(
        name="ecb",
        url="https://www.ecb.europa.eu/rss/press.html",
        kind=NewsSourceKind.MACRO,
    ),
)


@dataclass(frozen=True)
class NewsConfig:
    """资讯通道的采集、预筛与留痕参数。

    这一组管的是**输入**：从哪些源、取多久以内的条目、留下多少、把多长的正文送给
    LLM。资讯**结论**的形状（方向 / 是否抬升 / 强度）不在这里——那是 LLM 的输出，
    不是可调的旋钮；把「抬升阈值」做进配置面，等于给这套机制留一个能被调松的阀门。

    ## 采集窗口是**增量区间**，不是「最近 N 小时」

    窗口 = (上次判定成功时刻, 本次判定时刻]，上限 `window_cap_hours`（96 小时）兜住
    「判定停了几天又恢复」：没有上限，一次长故障后的重启会把攒了两周的条目一口气
    送进 LLM，而那一批得出的结论描述的是两周前的市场——它会被当成今天的结论生效。

    下限 `window_floor_hours`（24 小时）管的是反方向：判定成功后又跑一次（同一天里
    的重试、或当天第二次心跳）时，窗口只有几分钟。此时真正的语义是「今天这一天有
    什么新资讯」，不是「最近这五分钟」。下限把窗口钉在一天以上，于是同一天里的重复
    运行看到的是同一批条目——配合条目表的链接去重，重复运行不会重复送同一篇，
    只会得出同一个结论，这让整条通道可以幂等地重试。

    两个数配反了（上限 < 下限）会让区间永不到头或直接为空，而运行时的表现是
    「每天都 0 条」——安静得看不出是配错了，所以在 import 时校验。

    ## 预筛是**硬过滤 + 条数上限**，不是排序取前 N

    关键词不命中就直接丢，条数上限只在窗口很长（如上述故障恢复）时才咬人，咬的时候
    按发布时间倒序取最新的——「来得及反应」的那批。上限存在的理由不是成本，而是
    **LLM 的判断力**：一百条混在一起的一次性判断，比二十条时更容易滑向「没什么大事」。
    """

    # ---- 采集窗口（小时） ----
    window_floor_hours: int = 24
    window_cap_hours: int = 96

    # ---- 单源抓取 ----
    # 每个源的超时是**独立**的：一个源挂住不该让整轮采集陪葬，所以逐源 try/except
    # 并逐源记结果（「某个源抓取失败」必须能被单独看见，见模块 docstring）。
    fetch_timeout_seconds: float = 20.0
    # 单源取回条目数的上限，防止某个源一次给几千条把内存与预筛成本抬起来。
    max_items_per_source: int = 40

    # ---- 预筛 ----
    keywords: tuple[str, ...] = NEWS_KEYWORDS
    max_items_to_llm: int = 20
    # 送给 LLM 的正文截断上限。**这就是永久留存的那份原文**（截断后的），
    # 因为留存的是「当时 LLM 到底看到了什么」。将来体积真成问题时，正确的动作是
    # 下调这个数，不是删旧条目（CONTEXT.md 决策）。
    body_max_chars: int = 2000

    # ---- 白名单源 ----
    sources: tuple[NewsSource, ...] = NEWS_SOURCES

    def __post_init__(self) -> None:
        if self.window_floor_hours < 1:
            raise ValueError(
                f"window_floor_hours 必须 >= 1，当前 {self.window_floor_hours}"
                "——下限为 0 时窗口退化成「这一刻之后」，等于永远没有输入"
            )
        if self.window_cap_hours < self.window_floor_hours:
            raise ValueError(
                f"window_cap_hours({self.window_cap_hours}) 不能小于 "
                f"window_floor_hours({self.window_floor_hours})"
                "——区间会变成空的，而表现是「每天都 0 条」"
            )
        if self.fetch_timeout_seconds <= 0:
            raise ValueError(
                f"fetch_timeout_seconds 必须为正，当前 {self.fetch_timeout_seconds}"
            )
        for name in ("max_items_per_source", "max_items_to_llm", "body_max_chars"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} 必须 >= 1，当前 {getattr(self, name)}")
        if not self.keywords:
            raise ValueError(
                "keywords 不能为空——空词表等于「所有条目都命中」，"
                "预筛这道成本闸门会静默消失"
            )
        for kw in self.keywords:
            if kw != kw.strip() or kw.lower() != kw or not kw:
                raise ValueError(
                    f"关键词必须是非空、无首尾空白的小写串，当前 {kw!r}"
                    "——匹配是小写子串匹配，词表里出现大写等于这个词永远不命中"
                )
        if not self.sources:
            raise ValueError(
                "sources 不能为空——没有源就没有输入，而「没有输入」与「今天 0 条」"
                "在运行时会表现成同一件事"
            )
        names = [s.name for s in self.sources]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(
                f"资讯源重名：{duplicates}——来源名是条目与判定留痕里的标识，"
                "重名会让「这条来自哪个源」无法回答"
            )

    @property
    def kinds_covered(self) -> tuple[str, ...]:
        """当前白名单实际覆盖到的类别（按 `NewsSourceKind` 的声明顺序）。

        **派生而不是字段**：它是 `sources` 的函数，单独存一份就会与 `sources` 漂移，
        而漂移的表现是「日报说三类齐全，实际有一类一个源都没有」。写进快照，是为了
        让每条判定记录都能回答「当时是哪几类在供数」。

        它**不保证三类齐全**：某类被整个摘掉（源全挂了、被移出白名单）时，这里就少
        一项，而「少一项」与「这一类今天 0 条」是两件不同的事——前者是白名单的空洞，
        后者是正常输入，两者都会让那一天的相关资讯缺失，但只有前者需要人去补源。
        """
        covered = {s.kind for s in self.sources}
        return tuple(m.value for m in NewsSourceKind if m in covered)


CANDLES = CandleConfig()
JUDGEMENT = JudgementConfig()
JUDGEMENT_LIFECYCLE = JudgementLifecycleConfig()
EVIDENCE = EvidenceConfig()
DEACTIVATION = DeactivationConfig()
NEWS = NewsConfig()


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
    "deactivation": DEACTIVATION,
    "news": NEWS,
}

#: 派生属性（`asdict` 里没有，但同样是「当时生效的参数」）按分组点名补进快照。
#: 点名而不是「扫描所有 property」：自动扫描会把纯粹的便利属性（如 `__repr__` 之外的
#: 内部辅助量）也塞进落库契约，而落库契约只应该包含**解释结论时需要的那几个量**。
DERIVED_IN_SNAPSHOT: dict[str, tuple[str, ...]] = {
    "judgement": ("warmup_days",),
    "news": ("kinds_covered",),
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
        # 依赖预热长度；「当时覆盖了哪几类资讯源」也只能从这里读到），所以显式补进去。
        for extra in DERIVED_IN_SNAPSHOT.get(name, ()):
            if extra in result[name]:
                continue
            value = getattr(group, extra, None)
            if value is not None:
                result[name][extra] = _jsonable(value)
    return result


# 全部参数组的合并快照：判定记录用这个，省得逐次点名。
def full_snapshot() -> dict[str, dict]:
    """所有已登记分组的快照。判定记录直接内嵌它。"""
    return snapshot(*GROUPS)


def dumps(*names: str) -> str:
    """快照的 JSON 字面量（排序键，便于比对两条记录是否同参）。"""
    return json.dumps(snapshot(*names) if names else full_snapshot(),
                      ensure_ascii=False, sort_keys=True)
