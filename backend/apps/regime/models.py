"""行情阶段机制的持久化模型。

本模块只落**原始事实**，不落任何标签：日线标签是判定算法的确定性函数，落库会
制造第二个真相——算法一改，库里那批标签就是错的，而它们看起来完全正常，这是最
难发现的一类错误（CONTEXT.md 决策）。

`RegimeJudgement` 不违背这条纪律，因为它不是「某一天属于哪个阶段」的标签，而是
一次判定的**事件记录**：机制在某个时刻得出了什么结论、依据哪条 K 线、用的是哪套
参数、从什么时候开始生效。判定标签可以现算（同一段 K 线 + 同一套参数 ⇒ 同一条
结论），而这条记录不能：它的输入里有一部分不可重放（当时的参数快照、将来还要
加上当天的资讯条目），事后重跑只会得到「今天的算法对昨天的看法」。所以这里存的
是「当时到底说了什么」这个事实本身。两者是同一条纪律的两个方向——**能确定性重算
的不要存，不能重放的必须存**。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum

from django.db import models

from apps.common.time_utils import business_tz
from apps.regime.config import NewsSourceKind
from apps.regime.quant import BaseRegime

# 必须与下面 DecimalField 的 decimal_places 一致：写入方按这个精度量化后再落库，
# 否则「刚从交易所解析出的值」与「从库里读回的值」不相等，幂等同步会每次运行都
# 报「已更新」——同步看起来在工作，实际上一直在重写同一批数据。
DECIMAL_QUANTUM = Decimal("0.00000001")


class DailyCandle(models.Model):
    """一枚**已收盘**的日线，只存原始 OHLCV。

    两条刻意的取舍：

    1. **不存标签、不存派生指标**。阶段标签由判定算法从原始 OHLCV 现算；存了就
       等于把「日期 → 阶段」标签表从唯一真相降级成两份可能互相矛盾的答案。

    2. **同时存 `open_time` 与 `date`，明知冗余**。`open_time` 是交易所返回的原始
       时刻（Binance 日线固定 00:00 UTC 开盘），`date` 是它与判定/切片共用的自然日
       键。留 `date` 是为了让「日期 → 阶段」的关联是一次等值查询而不是时区换算，
       并把「业务日 = UTC 开盘日 = 北京时间同一日」这层口径写在表上，而不是留给读者
       自己推。两者由同一个写入方（candles.py）从同一个毫秒时间戳导出，
       `date == open_time.astimezone(utc).date()` 由测试钉住。
    """

    symbol = models.CharField("标的", max_length=32)
    date = models.DateField("自然日（UTC 开盘日 = 北京时间同日）")
    open_time = models.DateTimeField("开盘时刻（UTC）")

    open = models.DecimalField("开盘价", max_digits=20, decimal_places=8)
    high = models.DecimalField("最高价", max_digits=20, decimal_places=8)
    low = models.DecimalField("最低价", max_digits=20, decimal_places=8)
    close = models.DecimalField("收盘价", max_digits=20, decimal_places=8)
    volume = models.DecimalField(
        "成交量（基础币）", max_digits=30, decimal_places=8
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "regime_daily_candles"
        verbose_name = "日线（原始 OHLCV）"
        verbose_name_plural = "日线（原始 OHLCV）"
        ordering = ["symbol", "date"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "date"], name="uniq_daily_candle_symbol_date"
            )
        ]

    def __str__(self) -> str:
        return f"{self.symbol} {self.date} close={self.close}"


class NewsItem(models.Model):
    """一条**采集到的**资讯条目原文（永久保留）。

    这张表存的是「采集器看到了什么」，与「判定引用了什么」是两件事，所以必须分开：
    每天读进来的条目远多于送进 LLM 的（预筛会砍掉大部分），而**没入选的条目同样要留**
    ——「今天为什么没判出抬升」的答案有一半在没送进去的那批里。判定记录上的 `news_ref`
    只装被引用到的那几条与结论，两者由链接关联，不互相复制。

    唯一键是 `url`：同一条资讯在多个来源、多天里重复出现是常态，去重必须落在一个
    **跨天的持久集合**上（进程内去重在每次运行都从零开始，等于没有）。于是
    「今天 0 条」= 窗口内没有未见过的新条目，这与「某个源抓取失败」是两回事：后者是
    通道故障，由采集轮次的**逐源**结果回答，不能混进这个数字。

    `body` 存的是**送给 LLM 的那份文本**（按 `config.NEWS.body_max_chars` 截断后），
    不是站点原文的完整副本。存「LLM 当时到底看到了什么」而不是「站点上有过什么」：
    日报与复查要回答的是「依据这条资讯判出抬升，合理吗」，而那一刻可见的事实就是这
    段文字。`body_truncated` 让「原文就这么短」与「我们只取了前 N 字」可分辨——
    少了它，一条被截断到关键句之前的条目看起来就像一条无关的短讯。

    这张表**只增不删**（CONTEXT.md：资讯条目原文永久保留）。将来体积真成问题时，
    正确的动作是下调正文截断上限，不是删旧记录。
    """

    source = models.CharField("来源（白名单源名）", max_length=64, db_index=True)
    kind = models.CharField(
        "来源类别",
        max_length=16,
        choices=NewsSourceKind.choices(),
    )
    url = models.URLField("链接", max_length=2048, unique=True)
    title = models.CharField("标题", max_length=512)
    published_at = models.DateTimeField(
        "发布时刻", null=True, blank=True, db_index=True
    )
    body = models.TextField("正文（截断后，送给 LLM 的原文）", blank=True, default="")
    body_truncated = models.BooleanField("正文是否被截断", default=False)

    # 采集时刻而不是「业务日」：窗口是增量区间（上次判定成功 → 本次判定），
    # 它的边界是绝对时刻，不是自然日界，所以这里也记绝对时刻。
    fetched_at = models.DateTimeField("采集时刻", db_index=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_news_items"
        verbose_name = "资讯条目（原始，永久保留）"
        verbose_name_plural = "资讯条目（原始，永久保留）"
        ordering = ["-fetched_at", "-id"]

    def __str__(self) -> str:
        return f"[{self.source}] {self.title[:40]}"


class Escalation(str, Enum):
    """抬升标志：把基础阶段推向更保守方向的那个原因。

    与 `BaseRegime` 同一约定（ascii slug + 中文 `display`），空字符串表示「无抬升」，
    因此它可以直接作为 `CharField` 的取值与 `choices` 一起落库。

    做成枚举而不是布尔，是因为「有没有被抬升」与「被什么抬升」迟早要分开：事件熔断
    （第②段）是另一个会让系统更保守的输入，而它绝不能被记成「资讯抬升」——日报要写的
    是「今天为什么更保守」，原因串了，日报就变成了错的信息。
    """

    NEWS = "news"

    @property
    def display(self) -> str:
        return _ESCALATION_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [("", "无"), *((m.value, m.display) for m in cls)]


_ESCALATION_DISPLAY = {Escalation.NEWS: "资讯抬升"}

#: 空串的展示名，落在模型 choices 里。查询/日报要显示「无抬升」时用它，不要就地写中文。
NO_ESCALATION_DISPLAY = "无"


def business_midnight(day: date) -> datetime:
    """北京时间 `day` 当天 08:00 对应的绝对时刻（业务日的日界）。

    08:00 是业务日的边界而不是随便挑的钟点：北京时间 08:00 恰好是 UTC 00:00，也就是
    BTC 日线换线的那一刻，于是「运行日 D / 签署日 D−1 / 生效时刻」三者对齐到同一套
    自然日上，不需要在判定链路里做任何跨日推断。

    显式构造业务时区而不是写 `datetime(..., tzinfo=utc)`：偏移量今天是 +8 不代表永远
    是 +8（改偏移等于改全部历史生效时刻的含义），而 `apps/common/time_utils.py` 是
    「业务时区」的唯一口径。
    """
    return datetime.combine(day, time(8, 0), tzinfo=business_tz())


class RegimeJudgement(models.Model):
    """一次行情阶段判定的事件记录。

    三值（`base_regime` / `escalation` / `effective_regime`）**同时留存**是 CONTEXT.md
    钉死的要求：合成一个字段就再也回答不了「今天这个阶段是量化判出来的，还是资讯抬上来
    的」，而这正是判定出错时唯一的归因线索。两根轴因此必须各占一列，哪怕 v1 里
    `effective_regime` 恒等于「基础阶段被资讯抬升后的值」。

    **`attribute_date` 与 `effective_at` 都存在，明知它们今天由同一个映射导出**
    （`effective_at` = 签署日 + 2 天的北京 08:00）。理由是这张表要能扛住调度口径的变化：
    运行时刻一旦改动（比如从北京 08:00 挪到 12:00），签署日与新运行日的对应关系就变了，
    而**历史记录必须继续说它当时的意思**。派生出来的东西会跟着新映射一起改写全部历史，
    存下来的则是一枚不会被重新解释的化石。这与 `config_snapshot` 同一条纪律：留痕靠
    记录自己，不靠用今天的规则去反推。

    `attribute_date` 允许等于 `effective_at` 所在自然日减 2 天以外的值——模型层不校验
    这个映射（校验它等于把当前调度口径焊进表结构），映射由 `regime_judgement` 服务
    统一实现并由测试钉住。
    """

    symbol = models.CharField("标的", max_length=32)

    attribute_date = models.DateField(
        "签署日（作出该结论所依据的最后一条已收盘日线）", db_index=True
    )
    effective_at = models.DateTimeField("生效时刻（UTC）", db_index=True)

    base_regime = models.CharField(
        "基础阶段（纯量化口径）",
        max_length=16,
        choices=[(m.value, m.display) for m in BaseRegime],
    )
    escalation = models.CharField(
        "抬升标志",
        max_length=16,
        choices=Escalation.choices(),
        blank=True,
        default="",
    )
    effective_regime = models.CharField(
        "生效阶段（基础阶段被抬升后的值）",
        max_length=16,
        choices=[(m.value, m.display) for m in BaseRegime],
    )

    evidence = models.JSONField(
        "依据数字（量化原始输出 + 从输出到基础阶段之间发生的事）", default=dict
    )
    config_snapshot = models.JSONField(
        "写入时的完整参数快照（config.full_snapshot）", default=dict
    )
    news_ref = models.JSONField(
        "引用的资讯条目与资讯结论（单元 5 落地）", null=True, blank=True
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_judgements"
        verbose_name = "行情阶段判定记录"
        verbose_name_plural = "行情阶段判定记录"
        ordering = ["symbol", "-effective_at"]
        constraints = [
            # 幂等的唯一依据：一条记录由「标的 + 生效时刻」唯一确定，而生效时刻是运行日
            # 的函数。同一天重复执行（心跳每 5 分钟一次）因此只会命中同一条记录，不会
            # 因为「跑了几次」而多出几条结论。
            models.UniqueConstraint(
                fields=["symbol", "effective_at"],
                name="uniq_regime_judgement_symbol_effective_at",
            )
        ]

    @property
    def run_day(self) -> date:
        """得出该结论的名义运行日：签署日的次日。

        是**名义**值，不是实际执行时刻：心跳可能在北京 08:03 才跑到，但记录里不留这
        3 分钟。让抖动进不了数据，同一天的记录才不会因为「这次是第几个 tick 跑成的」
        而不同。
        """
        return self.attribute_date + timedelta(days=1)

    @property
    def run_at(self) -> datetime:
        """名义运行时刻（= 生效时刻前一天的业务日界）。

        唯一的用途是回答「机制多久没出结论了」：拿它跟现在比，得到的是结论的新鲜度。
        用 `effective_at` 直接比会得到负数（最新一条的正常状态是「明天生效」），用
        `attribute_date` 比则要额外记一层「正常滞后几天」的偏移。
        """
        return business_midnight(self.run_day)

    def __str__(self) -> str:
        return (
            f"{self.symbol} {self.attribute_date} "
            f"{self.effective_regime}@{self.effective_at:%Y-%m-%dT%H:%MZ}"
        )
