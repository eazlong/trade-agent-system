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

池化表（`RegimePoolRebuild` / `RegimePoolCell`，单元 7）同样不违背它，理由比
`RegimeJudgement` 更直白：池化结论**看似**可以从切片重算，但切片本身是会漂的
（判定算法一调，全部标签漂移；回测重跑，成交换一批），所以「当时依据哪几个回测、
多少笔、什么区间」重算不出来——而 CONTEXT.md 把这条定为**硬要求**：停用是全自动的，
每条停用决策都必须能回答「为什么停我」。世代（`rebuild`）是这条纪律的形状：
结论不覆盖，新一代挂在旁边，旧一代原样留着。

## 这个模块里为什么没有「日期 → 阶段」那种表

因为它可以现算（`slice.build_tags`），而池化表不能——两者的分别不在「是不是派生
量」，而在**重算的输入还在不在**。同一句判据，两次落地。
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


# --------------------------------------------------------------------------- #
# 池化表（单元 7）
# --------------------------------------------------------------------------- #
#
# 形状是**世代**（CONTEXT.md：「必须先失效后重建…重建完成后原子替换」）。一代是一次
# 重算的产物，落成一个 `RegimePoolRebuild` 行，格子挂在它下面。读者永远只读最新一代
# 的 `ready`，所以「跑到一半的表」不存在——不是靠加锁，是靠它压根不可见。
#
# 为什么不给格子加个 `is_current` 布尔让大家原地更新：那正是「重算跑到一半，每日判定
# 读到新旧混合的表」的形状，而那一条是 CONTEXT.md 点名要避免的。原地更新的第二个
# 代价是旧结论当场消失，而「重算前后的结论差异条数」需要两代同时在世才数得出来。


class PoolSource(str, Enum):
    """池化结论的来源：这条结论是从**这个策略自己的**切片来的，还是从原型兜底的。

    只做标记不做分支：日报要能说「这一格的结论是拿同原型其它策略的交易池出来的」，
    因为那种结论的说服力量级完全不同（它没有回答「这条策略」在做什么，只回答了
    「这类策略」在做什么）。
    """

    STRATEGY = "strategy"
    ARCHETYPE = "archetype"

    @property
    def display(self) -> str:
        return _POOL_SOURCE_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_POOL_SOURCE_DISPLAY = {
    PoolSource.STRATEGY: "本策略切片",
    PoolSource.ARCHETYPE: "原型兜底",
}


class RebuildStatus(str, Enum):
    """一代池化表的生命周期。

    `BUILDING` 是「重算中」在存储层的写法：它存在，但**读者看不见它**（只读 `READY`）。
    中断在中间的那一代会永远停在 `BUILDING`——这不是需要清理的垃圾，它是一条记录：
    「某次重算没跑完」。清掉它等于把一次失败也清掉了。
    """

    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"

    @property
    def display(self) -> str:
        return _REBUILD_STATUS_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_REBUILD_STATUS_DISPLAY = {
    RebuildStatus.BUILDING: "重算中",
    RebuildStatus.READY: "已就绪",
    RebuildStatus.FAILED: "失败",
}


class ActorKind(str, Enum):
    """触发是谁：人敲的命令，还是定时任务自己跑的。

    与 `actor_name` 分开而不是合一个自由文本字段，是因为两者的**可信度不同**：`task`
    行的 `actor_name` 是写死的任务名，`cli` 行的是 `getpass.getuser()`——一个可能被
    `--actor` 覆盖、也可能随容器里的 `USER` 环境变量变化的字符串。要看「人做了什么」
    就得先能把这批人挑出来，靠解析自由文本做不到。
    """

    CLI = "cli"
    TASK = "task"

    @property
    def display(self) -> str:
        return _ACTOR_KIND_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_ACTOR_KIND_DISPLAY = {ActorKind.CLI: "命令行", ActorKind.TASK: "定时任务"}


class RegimePoolRebuild(models.Model):
    """一次池化表重算的记录（一代）。

    CONTEXT.md 把这张表定为**每次重算必落一条**，理由与切片重算入口同源：它改变的是
    机制**全部行为依据**，且与回测数据解耦——没人重算时它永远不发生，发生时不看记录
    也永远不知道。所以这里的字段是按「事后有人问『昨天池化表怎么变了』」来定的：
    谁触发的、什么时候、看了几个回测、用上几个、改了几格。

    `results_used < candidates` 是**正常**的，不是故障：候选里有切片陈旧的、有算不出
    归一化样本的（本金 ≤ 0）。差额去哪了要能回答，所以 `exclusions` 按原因分开计数，
    而不是记一个「跳过了几个」。
    """

    status = models.CharField(
        "状态", max_length=16, choices=RebuildStatus.choices()
    )

    actor_kind = models.CharField("触发方类别", max_length=16, choices=ActorKind.choices())
    actor_name = models.CharField("触发方", max_length=128)

    # 池化口径的版本（`pool.POOL_VERSION`）。它已经进了 `input_fingerprint`——这里是
    # 把它**照着可查**地再写一遍。两份不算「两处真相」：一个是用来比对的指纹（不可读），
    # 一个是用来回答「这一代是按哪版合并口径算的」。少了这一列，那个问题只能靠翻
    # 格子 evidence 里那一份份抄下来的版本号来回答，而格子会随换代被清掉。
    pool_version = models.IntegerField("池化口径版本")

    # 输入指纹：本次重算看进去的「哪几个切片、各自哪个版本」。它的唯一用途是回答
    # 「现在这一代是不是已经过时了」——不想为此把几百个 result id 也存一遍（那些在
    # 格子的 evidence 里有，一份就够）。**只存指纹不存清单**是刻意的：存了清单就会有
    # 两份可能不一致的输入记录，而指纹不一致时没有任何办法判断哪份是对的。
    input_fingerprint = models.CharField("输入指纹", max_length=64, db_index=True)

    candidates = models.IntegerField("候选切片数", default=0)
    results_used = models.IntegerField("实际采用数", default=0)
    cells_total = models.IntegerField("格子总数", default=0)
    cells_changed = models.IntegerField("相对上一代变化的格子数", default=0)

    exclusions = models.JSONField("排除计数（按原因）", default=dict)

    started_at = models.DateTimeField("开始时刻")
    finished_at = models.DateTimeField("结束时刻", null=True, blank=True)
    failure = models.TextField("失败原因", blank=True, default="")

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_pool_rebuilds"
        verbose_name = "池化表重算记录"
        verbose_name_plural = "池化表重算记录"
        ordering = ["-started_at", "-id"]
        constraints = [
            # 同一输入指纹只允许有一代就绪。重算是幂等的（同样的输入必然得到同样的
            # 结论），所以「同一指纹跑出第二份 ready」不是幂等性的证据，而是一次重复
            # 触发——它会让「当前是哪一代」变成一个靠 id 大小猜的问题。
            # `building` 不在此列：并发触发时两边都建，后完成的那一代撞上这条约束、
            # 报错退出，正是想要的（重复触发不该悄悄产生第二代）。
            models.UniqueConstraint(
                fields=["input_fingerprint"],
                condition=models.Q(status="ready"),
                name="uniq_pool_rebuild_ready_fingerprint",
            )
        ]

    def __str__(self) -> str:
        return (
            f"#{self.pk} {self.status} {self.actor_kind}:{self.actor_name} "
            f"{self.cells_total}格"
        )


class RegimePoolCell(models.Model):
    """池化后的适用性结论，一个（策略 × 阶段）。

    键是（策略 × 阶段），但 CONTEXT.md 要求**记录到**（策略 × 品种 × 阶段）——两个
    都对，它们是两件事：决策按（策略 × 阶段）池化（「这条策略在下行趋势里行不行」不
    该因为跑在 BTC 还是 ETH 而给出两个答案），而池化的输入必须留下品种维度的痕迹，
    否则「结论与某品种独立表现明显冲突」这句判据在库里无处可查。所以品种不进键，
    进 `evidence["symbols"]`。

    `evidence` 是这张表的**存在理由**（模型 docstring 里那段）。它装的是：哪几个
    回测结果 id、各自多少笔、覆盖什么区间、合并后的成交数、以及原型的归一化留痕
    （原文字 + 命中词）。CONTEXT.md 的审计要求是「依据哪几个回测、多少笔、什么时间
    区间」——这三样都要能原样答出来，而重算它们需要当时的切片，切片是会漂的（判定
    算法一改全部标签漂移，回测重跑成交换一批），所以**必须存，不能现算**。
    """

    rebuild = models.ForeignKey(
        RegimePoolRebuild,
        on_delete=models.CASCADE,
        related_name="cells",
        verbose_name="所属世代",
    )
    strategy = models.ForeignKey(
        "trading.Strategy",
        on_delete=models.CASCADE,
        related_name="regime_pool_cells",
        verbose_name="策略",
    )
    regime = models.CharField(
        "阶段", max_length=16, choices=[(m.value, m.display) for m in BaseRegime]
    )

    source = models.CharField("结论来源", max_length=16, choices=PoolSource.choices())

    # 取值是 `slice.STATE_DISPLAY` / `slice.REASON_DISPLAY` 的键（**落库契约在那边**）。
    # 不写成 `choices` 是因为 `slice` 经由 `strategy_engine.indicators` →
    # `signal_monitor` 反向依赖模型，在这个模块里 import 成环。约束改由
    # `apps/regime/tests/test_pool.py` 钉住（词表与 choices 必须同集合），
    # 这里不引第二个真相。
    state = models.CharField("状态", max_length=32)
    reason = models.CharField("原因", max_length=32, blank=True, default="")

    evidence = models.JSONField("依据摘要（回测 id / 笔数 / 区间 / 品种 / 原型留痕）", default=dict)

    # 「明显冲突」的判据是 CONTEXT.md 定的（方向相反且两侧都过门槛）。冲突**不阻塞
    # 池化结论**，只阻塞停用建议：结论照常产出、照常被引用，但那格不产生建议，并标成
    # 待复核。所以这是一个独立的布尔，不是第七种 state——把它并进 state 就等于说
    # 「这格没有结论」，而它明明有。
    needs_review = models.BooleanField("待人工复核（冲突格子，不产生停用建议）", default=False)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_pool_cells"
        verbose_name = "池化适用性结论"
        verbose_name_plural = "池化适用性结论"
        ordering = ["strategy", "regime"]
        constraints = [
            # 一格只能有一条结论。原型兜底不是「额外多一条」，而是这一格**改用了**
            # 另一批样本——只有一条，由 `source` 说明它是哪种。
            models.UniqueConstraint(
                fields=["rebuild", "strategy", "regime"],
                name="uniq_pool_cell_rebuild_strategy_regime",
            )
        ]

    def __str__(self) -> str:
        return f"{self.strategy_id} × {self.regime} = {self.state}"


class DecisionStatus(str, Enum):
    """停用决策的状态。

    **单元 7 只会写出 `SUGGESTED` 这一个取值**，其余两个是第③段的 gate 层写的——
    这不是「先把枚举铺开」，而是 CONTEXT.md 那条边界的形状：第①段「只记建议，不施加」。
    把 `applied` 摆在这里，是为了让「这条决策到底停没停」永远有一个字段可问，而不是
    靠「有没有对应的 gate 行」去推——后者是一个能算错两次的问题。
    """

    SUGGESTED = "suggested"
    APPLIED = "applied"
    RELEASED = "released"

    @property
    def display(self) -> str:
        return _DECISION_STATUS_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_DECISION_STATUS_DISPLAY = {
    DecisionStatus.SUGGESTED: "建议中",
    DecisionStatus.APPLIED: "已施加",
    DecisionStatus.RELEASED: "已解除",
}


class DeactivationDecision(models.Model):
    """一条「这条策略在这个阶段不该跑」的结论记录。

    它由池化表推导出来，**但不是池化表的视图**——这个区别是整张表存在的理由。
    CONTEXT.md：停用是全自动的，所以每条停用决策都必须能回答「为什么停我」，而池化表
    会被下一代替换掉。判据成立**那一刻**的依据必须留在判据自己身上，不能留在会被重算
    覆盖的地方。所以这里冻结一份 `evidence`：当时是哪几个回测、多少笔、什么区间。

    重新推到同一条结论**不会新建一行**（键是策略 × 阶段）：结论没变就还是同一条决策，
    每天新建一行会让「这条策略被停了多少次」变成一个数不出来的数。取而代之，
    `last_confirmed_at` 每天往前走，而 `evidence` **不改**——它记的是判据成立那一刻，
    后来的确认只说明「今天还成立」，不构成改写历史的理由。这一点与 `RegimeJudgement`
    存 `attribute_date` 是同一条纪律。

    `exemption` 指向当时生效的人工豁免（若有）。存指针而不是每次现查，是因为豁免自己
    会过期：现查在豁免过期后就再也答不出「当初它是不是被豁免过」——而那正是用户
    会问的问题（「我不是恢复过它吗」）。
    """

    strategy = models.ForeignKey(
        "trading.Strategy",
        on_delete=models.PROTECT,
        related_name="regime_deactivation_decisions",
        verbose_name="策略",
    )
    regime = models.CharField(
        "阶段", max_length=16, choices=[(m.value, m.display) for m in BaseRegime]
    )

    status = models.CharField(
        "状态", max_length=16, choices=DecisionStatus.choices(), default=DecisionStatus.SUGGESTED.value
    )

    evidence = models.JSONField("判据成立时刻的依据摘要（冻结）", default=dict)

    #: 首次得出这条结论的池化世代。**留 id 不留外键**：世代会被清理，而对一条审计记录
    #: 来说「那一代没了」不该让它跟着一起消失（CASCADE 会），也不该拦住清理（PROTECT
    #: 会）。它是个线索，不是一条约束。
    pool_rebuild_id = models.IntegerField("首次得出时的池化世代 id", null=True, blank=True)

    exemption = models.ForeignKey(
        "regime.DeactivationExemption",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decisions",
        verbose_name="当时生效的人工豁免",
    )

    first_decided_at = models.DateTimeField("首次得出时刻")
    last_confirmed_at = models.DateTimeField("最近一次确认时刻")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_deactivation_decisions"
        verbose_name = "停用决策"
        verbose_name_plural = "停用决策"
        ordering = ["strategy", "regime"]
        constraints = [
            models.UniqueConstraint(
                fields=["strategy", "regime"],
                name="uniq_deactivation_decision_strategy_regime",
            )
        ]

    def __str__(self) -> str:
        return f"{self.strategy_id} × {self.regime}: {self.status}"


class ReviewVerdict(str, Enum):
    """重算差异的三种结论。

    `BECAME_FIT` 是要动的那一种：新表说这条策略在这个阶段其实可以跑，而它正被停着。
    CONTEXT.md 明令**不自动恢复**（「恢复需人工确认」是这份设计的地基），所以这个
    verdict 只做两件事：给决策记一条「依据已随重算失效」，并触发一次告知。
    """

    STILL_UNFIT = "still_unfit"
    STILL_NEUTRAL = "still_neutral"
    BECAME_FIT = "became_fit"

    @property
    def display(self) -> str:
        return _REVIEW_VERDICT_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_REVIEW_VERDICT_DISPLAY = {
    ReviewVerdict.STILL_UNFIT: "仍判为不适用",
    ReviewVerdict.STILL_NEUTRAL: "仍判为中性",
    ReviewVerdict.BECAME_FIT: "新表判为适用（依据已失效）",
}


class DeactivationReview(models.Model):
    """一次重算对一条**生效中**的停用决策的重放结果。

    挂在新一代旁边而不是改写决策本身，是 CONTEXT.md 的原话（「新表结论作为一条独立的
    重算差异记录挂在它旁边」）。覆盖会让「为什么当初停我」永远答不出来，而不记录会
    让「依据已失效」静默累积成一条看起来完全正常的停用——两种都是这份设计一路在清除
    的错误形状（`docs/adr/0001`）。

    只对**生效中的**决策重放（`status != RELEASED`）。已解除的决策重放没有意义：没有
    人在等它的答案。
    """

    decision = models.ForeignKey(
        DeactivationDecision,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name="被重放的决策",
    )
    rebuild = models.ForeignKey(
        RegimePoolRebuild,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name="依据的新世代",
    )

    verdict = models.CharField("重放结论", max_length=16, choices=ReviewVerdict.choices())
    evidence = models.JSONField("新表在这一格上的依据摘要", default=dict)

    reviewed_at = models.DateTimeField("重放时刻")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_deactivation_reviews"
        verbose_name = "停用决策重算差异"
        verbose_name_plural = "停用决策重算差异"
        ordering = ["-reviewed_at", "-id"]
        constraints = [
            # 同一次重算对同一条决策只重放一次。重放本身是幂等的，但重复落行会让
            # 日报第②段把同一条差异报两遍——而日报是这份设计里「告知」的主渠道，
            # 重复的告知比漏报更快让人开始忽略它。
            models.UniqueConstraint(
                fields=["decision", "rebuild"],
                name="uniq_deactivation_review_decision_rebuild",
            )
        ]

    def __str__(self) -> str:
        return f"{self.decision_id} @#{self.rebuild_id}: {self.verdict}"


class DeactivationExemption(models.Model):
    """人工确认恢复后产生的一条**有时效**的豁免。

    CONTEXT.md 说得直接：恢复不是一次性的开关动作，否则次日判定仍会说该策略在该阶段
    不适用，于是又把它停掉——用户确认一次、系统推翻一次，无限循环，每次循环还发一条
    通知。所以恢复的产物是这张表里的一行，而不是一个动作。

    两条失效条件取先到者：

    1. **到期**。`expires_at` 在**写入时就换算成绝对时刻存下来**（值来自
       `config.DEACTIVATION.exemption_days`）。改动配置因此**不会**追溯延长或缩短
       已经发出的豁免——与判定记录存 `attribute_date` 同一条理由：留痕靠记录自己，
       不靠用今天的规则去反推。
    2. **阶段离开**。由每日任务观测到「该阶段在任何受管品种上都不再是生效阶段」时
       写 `closed_at`。写成一条记录而不是纯查询派生，是因为**它必须让阶段回来时
       不再复活**：同日 10 天窗口内离开又回来，纯按 `now < expires_at` 判断会让
       豁免重新生效，而 CONTEXT.md 那句话是「自然失效」不是「暂停」。

    `expires_at` 因此是**发豁免那一刻的配置**，不是查询那一刻的配置——这句话要写进
    模型而不是留给读者推。
    """

    strategy = models.ForeignKey(
        "trading.Strategy",
        on_delete=models.PROTECT,
        related_name="regime_deactivation_exemptions",
        verbose_name="策略",
    )
    regime = models.CharField(
        "阶段", max_length=16, choices=[(m.value, m.display) for m in BaseRegime]
    )

    granted_at = models.DateTimeField("发出时刻")
    expires_at = models.DateTimeField("到期时刻（写入时按当时配置换算）", db_index=True)
    granted_by = models.CharField("发出人", max_length=128)
    note = models.TextField("备注", blank=True, default="")

    closed_at = models.DateTimeField("提前失效时刻（阶段离开）", null=True, blank=True)
    closed_reason = models.CharField("提前失效原因", max_length=32, blank=True, default="")

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_deactivation_exemptions"
        verbose_name = "人工恢复豁免"
        verbose_name_plural = "人工恢复豁免"
        ordering = ["strategy", "regime", "-granted_at"]

    def __str__(self) -> str:
        return f"{self.strategy_id} × {self.regime} 至 {self.expires_at:%Y-%m-%d}"


class ArchetypeOverride(models.Model):
    """Q5 的人工覆盖表：把某条策略的原型指到人指定的那一档。

    CONTEXT.md 把原型的归一化定为「代码内关键词映射表 + 数据库人工覆盖表」，并明确
    **不用 LLM 归类**（机制本体是确定性服务，LLM 只在「判定当前阶段」与「写解释」两步
    出场）。覆盖表是**数据不是阈值**，所以不进统一配置面——阈值要一起调、一起审计，
    而覆盖是一条条具体的事实纠正，粒度不同。

    **为什么必须有这张表**：真实策略的 `description` 是自由文本。库里现成的一条是
    「RSI 超卖区金叉买入，超买区死叉卖出」，它压根不套 `validate_description` 要求的
    四段模板。只做精确匹配（不归一化）时池化兜底几乎永远匹配不上，等于这个兜底不存在
    ——这正是 CONTEXT.md 点名的失败形状。

    键是**策略**而不是描述原文：描述会随策略迭代被改写，而人要纠正的是「这条策略属于
    哪一档」这个判断，不是「这段文字属于哪一档」。以原文为键会让一次无关的文案修改
    悄悄把纠正抹掉。
    """

    strategy = models.OneToOneField(
        "trading.Strategy",
        on_delete=models.CASCADE,
        related_name="regime_archetype_override",
        verbose_name="策略",
    )
    archetype = models.CharField("人工指定的原型", max_length=64)
    note = models.TextField("备注（为什么这么改）", blank=True, default="")
    created_by = models.CharField("修改人", max_length=128)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "regime_archetype_overrides"
        verbose_name = "策略原型人工覆盖"
        verbose_name_plural = "策略原型人工覆盖"
        ordering = ["strategy"]

    def __str__(self) -> str:
        return f"{self.strategy_id} → {self.archetype}"
