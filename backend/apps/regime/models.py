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

    `CHAT` 是单元 8ii 加的第三种（事件维护走 slash 命令，CONTEXT.md:152「不引入 Django
    admin」）。**它不与 `CLI` 合并**：聊天渠道的人没有 `getpass.getuser()`，能拿到的只有
    平台侧的 sender id，而那个 id 与系统用户名**指的不是同一个东西**——合并的话，
    「谁干的」这一栏会混进两种互不可比的标识。`actor_name` 的取值口径因此按 kind 走：
    `cli` 是系统用户名、`task` 是写死的任务名、`chat` 是平台 sender id。

    **每加一个成员都要动两张表**（`RegimePoolRebuild` 与 `RegimeMechanismSwitch` 的
    `choices` 会一起进迁移）。这是刻意付的代价：`choices` 只是校验面，合出来的是
    「谁干的」这一套词汇——分成两套枚举会让同一件事在两张表里叫两个名字。
    """

    CLI = "cli"
    TASK = "task"
    CHAT = "chat"

    @property
    def display(self) -> str:
        return _ACTOR_KIND_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_ACTOR_KIND_DISPLAY = {
    ActorKind.CLI: "命令行",
    ActorKind.TASK: "定时任务",
    ActorKind.CHAT: "聊天渠道",
}


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


# --------------------------------------------------------------------------- #
# 机制运行状态与 Shadow 每日记录（单元 8）
# --------------------------------------------------------------------------- #


class MechanismMode(str, Enum):
    """机制当前处在哪一档。

    分界是**机制有没有对市场施加动作**，不是「跑没跑起来」：Shadow 期判定、推导、
    日报全都照跑，只是不执行（CONTEXT.md:172「Shadow 期不执行任何市场动作 = 机制不
    施加动作」）。所以这一档说的不是健康状况，拿它当健康指标会读出反的结论。
    """

    SHADOW = "shadow"
    EXECUTING = "executing"

    @property
    def display(self) -> str:
        return _MECHANISM_MODE_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_MECHANISM_MODE_DISPLAY = {
    MechanismMode.SHADOW: "Shadow（只记录，不执行）",
    MechanismMode.EXECUTING: "执行态",
}


class RegimeMechanismSwitch(models.Model):
    """机制在 Shadow / 执行态之间的每一次切换，只增不改。

    形状是**流水**而不是一行「当前状态」，这是刻意的。一行状态表 + 一张流水表就是两份
    可以互相矛盾的真相，而它们的分歧（「状态说 shadow，最后一条流水说 executing」）恰好
    出现在切换写了一半的时候；只有流水时，这种分歧不存在。于是：

    - **当前档 = 最后一条的 `to_mode`**（`current()`）；
    - **一条流水都没有 = `SHADOW`**。机制出厂就在 Shadow，这是一个不需要被写下来的
      事实——为它落一行「初始切换」只会让「切过几次」多算一次。

    **单元 8 没有任何代码路径会创建这张表的行**，所以当前档恒为 `SHADOW`。「出 Shadow」
    是一个要人确认的动作（CONTEXT.md:160），它属于第③段。这里刻意把读取实现成「查流水」
    而不是写死 `SHADOW`：写死的话，第③段那条命令落了库而日报仍报 shadow，两边都「正常」，
    只能靠人去比对才发现——那正是本仓库反复避免的失败形状。

    留痕四件事：什么时候、谁、从哪档到哪档、为什么。`reason` 必填而不是备注，因为
    CONTEXT.md:161 的「自熔断退回 Shadow 后再回执行态，必须显式记录『这次是自熔断后的
    恢复』」最终就落在这个字段上；允许留空的字段拦不住「忘了写原因」。
    """

    from_mode = models.CharField("切换前档位", max_length=16, choices=MechanismMode.choices())
    to_mode = models.CharField("切换后档位", max_length=16, choices=MechanismMode.choices())

    at = models.DateTimeField("切换时刻（UTC）", db_index=True)

    # 复用 `ActorKind`：切换的两个来源正是「人敲的命令」与「机制自己（自熔断）」，与
    # 重算记录的触发方是同一组分别。再开一个枚举会让「谁干的」出现两套词汇。
    actor_kind = models.CharField("触发方类别", max_length=16, choices=ActorKind.choices())
    actor_name = models.CharField("触发方", max_length=128)

    reason = models.TextField("切换原因（必填）")

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_mechanism_switches"
        verbose_name = "机制档位切换记录"
        verbose_name_plural = "机制档位切换记录"
        ordering = ["-at", "-id"]

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d} {self.from_mode}→{self.to_mode} by {self.actor_name}"

    @classmethod
    def latest(cls) -> "RegimeMechanismSwitch | None":
        """最近一次切换；从未切换过返回 `None`。"""
        return cls.objects.order_by("-at", "-id").first()

    @classmethod
    def current(cls) -> MechanismMode:
        """当前档位。按 `at` 而不是按 id 排序：切换时刻是可回填的既有事实（补录一次
        历史切换），而 id 只反映写入顺序，两者在补录时会给出相反的答案。"""
        row = cls.latest()
        return MechanismMode(row.to_mode) if row is not None else MechanismMode.SHADOW


class ShadowDailyRecord(models.Model):
    """Shadow 期的每日一条记录（CONTEXT.md:162）。

    这张表存在的理由不是「留个日志」：Shadow 期机制不施加任何动作，于是机制**唯一**
    的产出就是这些行。三件事都要靠它回答，缺一个这张表就得重做：

    1. **成功标准①②**（CONTEXT.md:163）：一致率的比对要拿「机制当天说了什么」，触发
       频率要拿「一年里有几天真的会施加动作」——两者都必须按**天**取到，而不是按心跳
       的 5 分钟一拍取到（心跳频次是调度参数，把它算进成功率里会让指标随调度改变）。
    2. **自熔断频率条款**（CONTEXT.md:167「连续 3 个月触发频率 > 20%」）的数据来源就是
       这张表。Shadow 期没有真实施加，所以「触发」只能读成「当天本来会施加」——也就是
       `suggested_count > 0` 的天数占比。
    3. **日报第②段的今昨比对**：昨天的建议集合与今天的做差（Q3）。

    ## 三值内联复制，而不是只留一个外键

    基础阶段 / 抬升标志 / 生效阶段**照抄一份**在本地（`judgement` 外键同时留作下钻）。
    CONTEXT.md:162 明确要求记录里同时留下这三个值，理由与 `RegimeJudgement` 存三值
    同源：合成或省略一个，就再也回答不了「那天那个阶段是量化判出来的，还是资讯抬上来的」。
    外键**可空**且用 `SET_NULL`（与 `DeactivationDecision.exemption` 同一取舍）：三值是
    自足的，外键是一条线索而不是一条约束——判定记录永久保留，但让一条审计记录跟着它
    一起不可删，是把「保留」变成了「不许动」。

    外键指向的**就是三值抄来的那一行**，所以「外键非空 ⟺ 三值非空」。不指向「当天生效
    的判定」：今天的判定按次日业务日界生效，于是今天生效的那条永远是昨天判的，指向它等于
    让外键与三值天天差一天——那种错位处处自洽，最难查（写入方的取数见
    `apps.regime.shadow`）。

    ## 一行一天，但「还没有结论」不算结论

    唯一键保证一天一条；哪一轮心跳的那一条见 `apps.regime.shadow`。判据是**这一行里有
    没有结论**：空三值的行是「截至那一刻还没有结论」的占位，可以被当天后续的心跳补写成
    结论行；有结论的行是定论，此后任何心跳都不改写它。所以读者看这张表时要问的不是
    「谁写的」，而是「这一行有没有结论」——`base_regime` 为空即**那一天机制最终没有出
    结论**（判定层的失败或数据不足，CONTEXT.md:83 的「保持上一有效状态」）。

    ## 建议为什么落成冻结清单，而不是引用决策行

    `DeactivationDecision` 的行是**原地更新**的（同一策略 × 阶段只有一行，
    `last_confirmed_at` 天天往前走）。今天的记录若只存决策 id，明天那条决策被改一次
    豁免、后天的「昨天建议了什么」就跟着变了——而第②段的今昨做差正是要发现这种变化。
    所以建议在这里**冻结**：当天的策略 id / 层 / 状态原样写进 JSON，此后谁都不改它。
    第②段（或任何读者）拿两天的清单做差，比的是两天的世界，不是同一个会动的东西。

    `suggested_count` 是同一行内 `suggestions` 长度的显式副本，**不算第二份真相**：
    它不描述世界，只描述这一行自己，且写入时与清单同生共死。留它的唯一理由是让
    「哪些天本来会施加动作」是一次 `filter(suggested_count__gt=0)` 而不是一次 JSON
    长度运算——自熔断条款要按 3 个月滚动扫，这句话会被执行很多次。

    `executed` 照落而**恒为 False**（CONTEXT.md:162「实际是否执行=否」）。不用「没有
    执行记录」来表达「没执行」：两者在读的人眼里一模一样，而其中一个是当时的承诺，
    另一个是数据的缺口。
    """

    symbol = models.CharField("标的", max_length=32)
    #: **不叫 `attribute_date`**，尽管 `RegimeJudgement` 用的是那个名字：那边是**签署日**
    #: （判定所依据的那条已收盘日线，运行日的前一天），这边是**运行日**。两张表的日期
    #: 看着能等值 join 而实际差一天，是最容易静默错位的一类 bug（第②段的今昨做差正是
    #: 按天对齐的）。用 `run_day` 这个词——它与 `RegimeJudgement.run_day` 属性是**同一个
    #: 东西**，也与判定返回值里的 `run_day` 同一个名字。
    run_day = models.DateField("运行日（业务时区的自然日）", db_index=True)

    judgement = models.ForeignKey(
        "regime.RegimeJudgement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shadow_records",
        verbose_name="三值所抄的那条判定（下钻用）",
    )

    base_regime = models.CharField(
        "基础阶段（内联副本）",
        max_length=16,
        choices=[(m.value, m.display) for m in BaseRegime],
        blank=True,
        default="",
    )
    escalation = models.CharField(
        "抬升标志（内联副本）",
        max_length=16,
        choices=Escalation.choices(),
        blank=True,
        default="",
    )
    effective_regime = models.CharField(
        "生效阶段（内联副本）",
        max_length=16,
        choices=[(m.value, m.display) for m in BaseRegime],
        blank=True,
        default="",
    )

    #: 推导那一层的收场（`deactivation_run` 的 `skipped`：cold_start / stale_state /
    #: no_generation，或空串表示正常推了）。用短代码而不是并进 `note`：日报第④段要**数**
    #: 「判定跑了但机制没表态」的天数，靠解析自由文本做不到。
    derivation_skipped = models.CharField(
        "推导收场（空 = 正常推导）", max_length=32, blank=True, default=""
    )
    note = models.TextField("给人看的一句话（为什么这一行是这样）", blank=True, default="")

    suggestions = models.JSONField("当天的建议清单（冻结）", default=list)
    suggested_count = models.IntegerField("建议条数（本行清单长度的副本）", default=0)

    executed = models.BooleanField("实际是否执行（第①段恒为否）", default=False)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_shadow_daily_records"
        verbose_name = "Shadow 每日记录"
        verbose_name_plural = "Shadow 每日记录"
        ordering = ["symbol", "-run_day"]
        constraints = [
            # 一天一条。这个唯一键是这张表能被当作「逐日序列」使用的前提：日报的今昨
            # 比对、频率条款的占比，分母都是「天数」，多出第二行就会静默地把某一天数两遍。
            # 因此写入方**不新建第二行**（心跳 5 分钟一轮，一天里绝大多数 tick 走到那里
            # 只会确认已有那一行）——但一天之内哪一轮心跳的内容留下来，不是「第一条」
            # 那么简单：见上面那一段与 `apps.regime.shadow`。
            models.UniqueConstraint(
                fields=["symbol", "run_day"],
                name="uniq_shadow_daily_symbol_run_day",
            )
        ]

    def __str__(self) -> str:
        return (
            f"{self.symbol} {self.run_day} "
            f"{self.effective_regime or '（无结论）'} 建议{self.suggested_count}"
        )


# --------------------------------------------------------------------------- #
# 重大事件与候选事件（第①段单元 8ii）
#
# 本段的边界（CONTEXT.md 的「第②段才做窗口与减仓」）：这里**只有维护入口的形状**——
# 一行是一条被人工录入或被确认过的事实，外加一条「谁在什么时候改了它」的流水。
# 窗口怎么被读取、熔断期内谁被停、减仓怎么叠，全是第②段的事，本模块一行都不写。
# --------------------------------------------------------------------------- #


class EventImpact(str, Enum):
    """事件的冲击档位。**只有「高」触发熔断**（CONTEXT.md 第 147 条）。

    三档而不是两档：要表达「这件事值得写在日报里、但不值得停掉全场」，需要一个中间档。
    而「要不要熔断」这件事**不是一个可配的阈值**——它是一个定义：高 = 足以在数小时内
    造成全市场 >5% 波动。把这条定义留在枚举上而不是配置里，是因为它一旦可调，「昨天
    这条事件为什么熔断了」就变成一个随配置漂移的问题。
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def display(self) -> str:
        return _EVENT_IMPACT_DISPLAY[self]

    @property
    def triggers_halt(self) -> bool:
        """本档是否触发熔断。写成属性而不是让调用方比字符串——那等于把这条规则
        复制到每一个读它的地方，而第②段与日报是两个读它的地方。"""
        return self is EventImpact.HIGH

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_EVENT_IMPACT_DISPLAY = {
    EventImpact.HIGH: "高",
    EventImpact.MEDIUM: "中",
    EventImpact.LOW: "低",
}


class EventScope(str, Enum):
    """事件的作用域：全市场，还是指定的品种列表。

    **必填，且没有「默认全市场」这条退路**（CONTEXT.md 第 147 条）：作用域决定了谁在
    窗口里被停，而「忘了填」与「确实影响全市场」在库里长得一模一样——一条本该只停
    SOL 的事件真按全市场执行，症状是「那天什么都被停了」，而那看起来像一次保守的胜利。
    """

    MARKET = "market"
    SYMBOLS = "symbols"

    @property
    def display(self) -> str:
        return _EVENT_SCOPE_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_EVENT_SCOPE_DISPLAY = {
    EventScope.MARKET: "全市场",
    EventScope.SYMBOLS: "指定品种",
}


class EventStatus(str, Enum):
    """重大事件的生命周期。**只有两个取值，且没有「已结束」**。

    取消是**改状态**而不是删行：删掉之后「这条事件曾经存在过、后来被人取消了」就再也
    答不出来，而窗口期内取消、窗口期后取消、从未生效过的取消是三件不同的事，它们的
    区别全靠这一行还在。至于「结束」——那是时间的函数（`resume_at` 过了就结束了），
    存下来只会多一份可以与时刻表矛盾的真相。
    """

    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"

    @property
    def display(self) -> str:
        return _EVENT_STATUS_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_EVENT_STATUS_DISPLAY = {
    EventStatus.SCHEDULED: "已排期",
    EventStatus.CANCELLED: "已取消",
}


class EventChangeKind(str, Enum):
    """一次维护动作的种类。流水条目用它来回答「刚才那条命令干了什么」。

    `IMPACT_CHANGED` 同时覆盖升档与降档：命令只有「提升为高」一条（CONTEXT.md 第 149
    条要求提升必须人工），但流水要是不记降档，一次「高 → 中」就会在历史里消失——
    而那正是「这条事件当初为什么熔断过」的答案。免得读者靠 before/after 两个 JSON 去
    猜是哪一档在动。
    """

    CREATED = "created"
    RESCHEDULED = "rescheduled"
    IMPACT_CHANGED = "impact_changed"
    CANCELLED = "cancelled"

    @property
    def display(self) -> str:
        return _EVENT_CHANGE_KIND_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_EVENT_CHANGE_KIND_DISPLAY = {
    EventChangeKind.CREATED: "录入",
    EventChangeKind.RESCHEDULED: "改期",
    EventChangeKind.IMPACT_CHANGED: "改档",
    EventChangeKind.CANCELLED: "取消",
}


class MajorEvent(models.Model):
    """一条**人工录入**（或经人工确认从候选转正）的重大事件（CONTEXT.md 第 147 条）。

    第一版只做**日历型**：时间能提前确定的事件。突发型那类「发生的一刻才知道」的事
    不在这里——它的形状是「事后立刻停」，与本表的「提前排一个窗口」是两套东西，塞进
    同一张表会让 `event_time` 同时表示「将要发生」和「已经发生」，而这两种时刻在读取
    端的处理正好相反。

    ## 窗口在录入那一刻算好，并**存下来**

    `halt_at` / `resume_at` 是录入时按当时的 `config.EVENTS` 算出来的绝对时刻，存成列
    而不是每次查询现算。理由与 `DeactivationExemption.expires_at` 同源：**改配置不得
    追溯改变一条已入库事件的窗口**。同一个 `event_time` 配不同的窗口就是两个不同的熔断
    区间；现算的话，某天有人把默认前 2 小时调成 4 小时，昨天那条事件的窗口就跟着变了，
    而它当时可能已经执行过动作——「那天为什么从 10:00 就停了」从此答不出来。

    单事件覆盖（`halt_before_minutes` / `resume_after_minutes`）只在录入端参与计算，
    落库时**与算出来的时刻一起留下**（见 `apps.regime.events.resolve_window`）：只存
    覆盖值的话，读的人仍然要拿今天的上下限去反推当时是否合法。

    ## 为什么 `symbols` 用 JSON 而不是一张关联表

    品种列表是这条事件的**一个属性**，不是一份会被别处引用的实体——没有「SOL 这个品种」
    这张表可挂，而挂到 `LiveSession` 上等于说「事件的作用域由当前在跑的会话决定」，
    那是反的。存成 JSON 列表的代价是查不了「哪些事件影响 SOL」这种反查；本段的读取
    路径（第②段的窗口判定、日报的「未来 N 条」）都是「先取事件、再问它影不影响谁」，
    方向正好是顺的。
    """

    name = models.CharField("事件名称", max_length=128)

    scope_kind = models.CharField(
        "作用域种类", max_length=16, choices=EventScope.choices()
    )
    #: 全市场时是空列表。**空列表与「没填」不是一回事**：作用域种类已经回答了「是不是
    #: 全市场」，这里为空只表示「不需要逐品种列出」。
    symbols = models.JSONField("作用域品种列表（全市场时为空）", default=list)

    #: 事件本身的绝对时刻。**存 UTC、录入按北京时间解释**（CONTEXT.md 第 149 条），
    #: 解释动作在 `apps.regime.events.parse_business_time`，模型只收算好的时刻。
    event_time = models.DateTimeField("事件时刻（UTC）", db_index=True)

    impact = models.CharField("冲击档位", max_length=16, choices=EventImpact.choices())

    halt_at = models.DateTimeField("停止时刻（录入时按当时配置算好）", db_index=True)
    resume_at = models.DateTimeField("恢复时刻（录入时按当时配置算好）", db_index=True)

    status = models.CharField(
        "状态", max_length=16, choices=EventStatus.choices(), default=EventStatus.SCHEDULED.value
    )

    created_by = models.CharField("录入人", max_length=128)
    note = models.TextField("备注", blank=True, default="")

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "regime_major_events"
        verbose_name = "重大事件"
        verbose_name_plural = "重大事件"
        # 按事件时刻排：读这张表的两个地方（第②段的窗口判定、日报的「未来 7 天」）问的
        # 都是「接下来会发生什么」，按 `event_time` 排是这个问题的自然索引。
        ordering = ["event_time", "id"]
        constraints = [
            # 窗口必须自洽。反过来的窗口不会报错，只会让「停」与「恢复」两条流水在
            # 日志里前后颠倒——而读取端届时会各自按自己的顺序解释它，两边都「正常」。
            models.CheckConstraint(
                check=models.Q(resume_at__gte=models.F("halt_at")),
                name="ck_major_event_window_ordered",
            )
        ]

    def __str__(self) -> str:
        return f"{self.event_time:%Y-%m-%d %H:%M} {self.name}（{self.impact_display}）"

    @property
    def impact_display(self) -> str:
        return EventImpact(self.impact).display

    @property
    def status_display(self) -> str:
        return EventStatus(self.status).display

    @property
    def triggers_halt(self) -> bool:
        """这条事件是否会开启熔断窗口。**取消掉的事件仍然返回 False**——把它并进
        调用方的判据里，是为了让「已取消的事件不产生窗口」只有一处实现。"""
        return (
            self.status == EventStatus.SCHEDULED.value
            and EventImpact(self.impact).triggers_halt
        )

    def applies_to(self, symbol: str) -> bool:
        """这条事件是否作用到某个品种上。全市场恒真。"""
        if self.scope_kind == EventScope.MARKET.value:
            return True
        return symbol in (self.symbols or [])

    @property
    def scope_display(self) -> str:
        if self.scope_kind == EventScope.MARKET.value:
            return EventScope.MARKET.display
        return "、".join(self.symbols or [])


class MajorEventChange(models.Model):
    """重大事件的维护流水，**只增不改**。

    形状与 `RegimeMechanismSwitch` 同源（流水而非一行「当前状态」）：`MajorEvent` 那一行
    本身是会被改的（改期、改档、取消），而「谁在什么时候把它改成了什么」必须留在不会被
    下一次修改覆盖的地方。

    为什么不能只靠 `updated_at`：它回答得了「什么时候被改过」，回答不了**改了什么**，
    也回答不了「改之前是什么」。而这三件事有两件被 CONTEXT.md 直接点名——第 149 条
    「提升为『高』必须人工，需要留痕」、第 154 条「事件改期与取消必须走命令并留痕」。
    留痕的对象是**动作**，不是行的最后状态。

    `before` / `after` 只装**被改动的键**，不整行快照：整行快照会让「这条流水改了什么」
    需要读者自己对照两行 JSON 求差，而求差的那个读者（人）正是这条流水存在的理由。
    只装被改动的键，也让「改期」与「改档」在同一条流水里天然可分辨。
    """

    event = models.ForeignKey(
        MajorEvent,
        on_delete=models.CASCADE,
        related_name="changes",
        verbose_name="所属事件",
    )

    kind = models.CharField("动作", max_length=24, choices=EventChangeKind.choices())

    #: 动作发生的绝对时刻。用独立字段而不是复用 `created_at`：补录一条历史动作时，
    #: 两者会不同——而「这条改期是什么时候批的」问的是前者。
    at = models.DateTimeField("动作时刻（UTC）", db_index=True)

    actor_kind = models.CharField("触发方类别", max_length=16, choices=ActorKind.choices())
    actor_name = models.CharField("触发方", max_length=128)

    before = models.JSONField("改动前（只含被改动的键）", default=dict)
    after = models.JSONField("改动后（只含被改动的键）", default=dict)

    note = models.TextField("备注", blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_major_event_changes"
        verbose_name = "重大事件维护流水"
        verbose_name_plural = "重大事件维护流水"
        ordering = ["-at", "-id"]

    def __str__(self) -> str:
        return f"#{self.event_id} {self.kind} by {self.actor_name}"


class CandidateOrigin(str, Enum):
    """候选事件的提出方。**两个取值不是分类，是两条不能合并的溯源。**

    CONTEXT.md 第 94 条要的是「Agent 的建议与资讯的建议走同一条出口」——同一条出口指的
    是**确认流程**相同，不是来源相同。合并成一个「系统建议」会让「这条是谁提的」在库里
    消失，而日报第③段末尾那一节要按来源分开列：资讯提的带着原文链接，Agent 提的带着
    当时那句对话。
    """

    NEWS = "news"
    AGENT = "agent"

    @property
    def display(self) -> str:
        return _CANDIDATE_ORIGIN_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_CANDIDATE_ORIGIN_DISPLAY = {
    CandidateOrigin.NEWS: "资讯判定",
    CandidateOrigin.AGENT: "Agent 建议",
}


class CandidateStatus(str, Enum):
    """候选事件的归宿。CONTEXT.md 第 37 条：**唯一出路是转正或失效丢弃**。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"

    @property
    def display(self) -> str:
        return _CANDIDATE_STATUS_DISPLAY[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.display) for m in cls]


_CANDIDATE_STATUS_DISPLAY = {
    CandidateStatus.PENDING: "待确认",
    CandidateStatus.CONFIRMED: "已转正",
    CandidateStatus.DISCARDED: "已丢弃",
}


#: 丢弃原因的取值。**写得下、问得出**就够了，所以是短代码而不是又一张枚举：
#: `EXPIRED` 是 14 天到了没人确认（自动），“rejected” 是人看了一眼说不要。
#: 两者的区别在日报里很要紧——前者说明**没人看**，后者说明**看过了**。
CANDIDATE_DISCARD_EXPIRED = "expired"
CANDIDATE_DISCARD_REJECTED = "rejected"
_CANDIDATE_DISCARD_REASON_DISPLAY = {
    CANDIDATE_DISCARD_EXPIRED: "到期未确认",
    CANDIDATE_DISCARD_REJECTED: "人工否决",
}


class CandidateEvent(models.Model):
    """一条**尚未入库**的建议事件（CONTEXT.md 第 37、94 条）。

    **它没有窗口、不产生任何熔断**——这一点是这张表与 `MajorEvent` 的分界线，也是它
    存在的理由：把「有人觉得下周有个大事」与「一条已经排期的熔断事件」放进同一张表，
    读的人就再也分不清「这条会不会真的停我」。所以它连 `halt_at` 都没有一列，要转正
    必须由人把时间、冲击档位、作用域**重新说一遍**（`events.confirm_candidate`）。

    14 天失效期写在 `expires_at` 上（写入时按当时的 `config.EVENTS.candidate_expiry_days`
    换算成绝对时刻），理由与 `DeactivationExemption.expires_at` 相同：改配置不得追溯
    延长或缩短一条已经提出的候选。**「到期」本身不删除任何行**，只把状态改成
    `discarded`——「这条建议提过、没人理它」正是覆盖率衰减的一个证据（第④段的提醒要靠
    它），删掉就等于把「机制提过但没人看」这件事抹了。

    `news_item` 只在 `origin=news` 时非空，用 `SET_NULL`：资讯条目永久保留，但让一条
    候选跟着它一起不可删，是把「保留」变成「不许动」——同 `ShadowDailyRecord.judgement`
    的取舍。
    """

    name = models.CharField("推测的事件名称", max_length=128)

    origin = models.CharField("提出方", max_length=16, choices=CandidateOrigin.choices())

    #: 推测时刻，**可空**：资讯里常常只说「下周」或「月末」，说不出具体钟点。
    #: 为它编一个时间等于给一个未知量填一个看起来像事实的数。
    guessed_time = models.DateTimeField("推测时刻（UTC，可空）", null=True, blank=True)

    #: 提出日期（业务日）。与可空的 `guessed_time` 分开：日报那一节要按「什么时候提的」
    #: 排序与计龄，而那一列不能因为事件时间未知就变成空的。
    raised_at = models.DateField("提出日期（业务日）")
    raised_by = models.CharField("提出方标识（资讯源名 / sender id）", max_length=128)

    news_item = models.ForeignKey(
        "regime.NewsItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="candidate_events",
        verbose_name="来源资讯条目",
    )
    note = models.TextField("备注（推测依据）", blank=True, default="")

    expires_at = models.DateTimeField("失效时刻（写入时按当时配置换算）", db_index=True)

    status = models.CharField(
        "状态",
        max_length=16,
        choices=CandidateStatus.choices(),
        default=CandidateStatus.PENDING.value,
    )
    decided_at = models.DateTimeField("处置时刻（UTC）", null=True, blank=True)
    decided_by = models.CharField("处置人", max_length=128, blank=True, default="")
    discard_reason = models.CharField(
        "丢弃原因（expired / rejected）", max_length=16, blank=True, default=""
    )

    #: 转正后产生的重大事件。存外键而不是反过来在 `MajorEvent` 上存候选 id：一条候选
    #: 至多转正一次（转正即终态），而反过来会让人以为一条事件只能有一个来源。
    confirmed_event = models.ForeignKey(
        MajorEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_from",
        verbose_name="转正后的重大事件",
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "regime_candidate_events"
        verbose_name = "候选事件（未入库）"
        verbose_name_plural = "候选事件（未入库）"
        ordering = ["-raised_at", "-id"]
        constraints = [
            # 终态不可回退：转正过的候选不能再被丢弃，否则同一条建议会同时说着
            # 「已转正」和「已丢弃」，而日报第③段两个集合都读。写成蕴含式
            # （「不是已转正」或「有指向的事件」），而不是把它拆成两条状态机规则。
            models.CheckConstraint(
                check=(
                    ~models.Q(status=CandidateStatus.CONFIRMED.value)
                    | models.Q(confirmed_event__isnull=False)
                ),
                name="ck_candidate_confirmed_has_event",
            )
        ]

    def __str__(self) -> str:
        return f"{self.raised_at} {self.name}（{self.status_display}）"

    @property
    def status_display(self) -> str:
        return CandidateStatus(self.status).display

    @property
    def discard_reason_display(self) -> str:
        return _CANDIDATE_DISCARD_REASON_DISPLAY.get(self.discard_reason, self.discard_reason)

    @property
    def is_pending(self) -> bool:
        return self.status == CandidateStatus.PENDING.value

    def is_expired(self, now=None) -> bool:
        """是否已过失效期。**只回答事实，不改状态**——到期改成 `discarded` 是一次
        写入，由每日的清理动作做（`events.expire_candidates`）。查询端用得到这个判断，
        因为「今天过期的」与「今天被丢弃的」之间隔着一次任务运行。"""
        from django.utils import timezone as _tz

        return (now or _tz.now()) >= self.expires_at


# --------------------------------------------------------------------------- #
# 每日日报（第①段单元 8iii）
#
# 本段只有「落库的形状」：内容怎么组稿、按人怎么裁剪，全在 `apps.regime.report`。
# 投递与投递看门狗是单元 8iv，所以这里**没有**投递那几列——先记「说了什么」，
# 再说「送到了没」；把两者塞进同一次写入，会让「今天日报没生成」与「生成了没送出去」
# 在库里长得一样，而这两件事的处置完全不同（前者是判定任务的问题，后者是投递通路）。
# --------------------------------------------------------------------------- #


class DailyReport(models.Model):
    """一天一条的日报（CONTEXT.md 第 173 条）。

    ## 为什么它是一张表，而不是一条日志

    日报被定为**每天固定一条、必发**（沉默必须能被识别为异常）。这句话落库才有意义：
    只有存下来，「今天没有日报」才是一个可以被查询的事实，而不是一个只能靠回忆判断的
    感觉。投递看门狗（8iv）判的就是「今天这张表里有没有一行成功投递」——它读的必须
    是**这张表**，不能是日报自己发的消息（CONTEXT.md:175「看门狗不能靠日报自己告警」）。

    ## 为什么 `landscape` 要落一份结构化快照

    `DeactivationDecision` 的行是**原地更新**的（同一（策略 × 阶段）一行，
    `last_confirmed_at` 天天往前走，单元 7 的决定）。于是「昨天建议停谁」这个问题的
    答案会随着时间被改写——今天去读决策表，读到的是今天的世界，不是昨天那句话。
    第②段的今昨做差要的恰恰是**两天各自说了什么**，所以每天把当时的推导结论
    （每层 → 建议集合，含状态）整份冻在这里。这与 `ShadowDailyRecord.suggestions`
    冻结清单是同一条纪律的两个粒度：那边冻一天的清单，这边冻一天的世界。

    缺了它，第②段的做差会退化成「把今天的世界与今天的世界相减」，永远得零——
    而那看起来像「机制很稳定」。

    ## 为什么 `sections` 与正文分开

    `sections` 是**结构化的五段**（每段是一段文字，第②段另附条目），`landscape` 是
    做差用的原料。正文（渲染出来的那一条消息）**不落库**：它是 `sections` 的函数，
    落一份就等于承认两份真相，而裁剪还是按人做的——同一个 `sections` 对每个人渲染出
    不同的正文。落库里的是「机制今天说了什么」，发出的是「你该看到哪一部分」。

    ## 三值内联 + 外键，与 `ShadowDailyRecord` 同取舍

    抄一份三值到本地，`judgement` 外键留作下钻，可空 + `SET_NULL`。理由那一整段在
    `ShadowDailyRecord` 里写过了，这里不重复；两处取舍必须一致，否则同一天的两张表
    会对「那天是量化判的还是要资讯抬的」给出两种读法。

    ## `judgement_missing` 为什么是一列

    判定任务失败或数据不足时日报**照发**，第①段明写「今日判定缺失，处于保持的上一
    有效状态」（CONTEXT.md:83）。这件事必须是一列而不是「三值为空即视为缺失」：
    「判定跑了、结论是空的」与「判定压根没跑」在库里都是空三值，而前者是算法的事、
    后者是任务的事。用一列显式的布尔把当时的语义钉住——库里没有第二处能补出这个区分。
    """

    symbol = models.CharField("标的", max_length=32)

    #: **运行日**（业务时区的自然日）：与 `ShadowDailyRecord.run_day` 同一个东西、
    #: 同一个名字。日报第①段报的是「今天刚产出、明日 08:00 才生效」的那条判定，
    #: 所以按天对齐两张表时用的是运行日，不是签署日（`RegimeJudgement.attribute_date`）。
    run_day = models.DateField("运行日（业务时区的自然日）", db_index=True)

    judgement = models.ForeignKey(
        "regime.RegimeJudgement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_reports",
        verbose_name="三值所抄的那条判定（下钻用）",
    )

    base_regime = models.CharField(
        "基础阶段（内联副本）",
        max_length=16,
        choices=[(m.value, m.display) for m in BaseRegime],
        blank=True,
        default="",
    )
    escalation = models.CharField(
        "抬升标志（内联副本）",
        max_length=16,
        choices=Escalation.choices(),
        blank=True,
        default="",
    )
    effective_regime = models.CharField(
        "生效阶段（内联副本）",
        max_length=16,
        choices=[(m.value, m.display) for m in BaseRegime],
        blank=True,
        default="",
    )

    #: 推导那一层的收场（`deactivation_run` 的 `skipped`：cold_start / stale_state /
    #: no_generation，或空串表示正常推了）。日报第④段要**数**「判定跑了但机制没表态」
    #: 的天数，靠解析自由文本做不到——同 `ShadowDailyRecord.derivation_skipped`。
    deactivation_skipped = models.CharField(
        "推导收场（空 = 正常推导）", max_length=32, blank=True, default=""
    )

    #: True = 今天到截止时刻仍没有判定结论，日报照发并明写「今日判定缺失」。
    judgement_missing = models.BooleanField("今日判定缺失（照发）", default=False)

    #: 五段的结构化内容：键是段名（today / change / events / health / delivery），
    #: 值是渲染好的那一段文字；第②段另带结构化条目，供按人裁剪。正文不落这里。
    sections = models.JSONField("五段内容（结构化）", default=dict)

    #: 当天的推导世界（每层 → 建议集合，含状态）。第②段今昨做差的**原料**：没有它，
    #: 做差只能拿两份都会被改写的决策行去比，等于永远比出「无变化」（见类 docstring）。
    landscape = models.JSONField("当天推导结论快照（做差用）", default=dict)

    note = models.TextField("给人看的一句话（为什么这一行是这样）", blank=True, default="")

    created_at = models.DateTimeField("生成时间", auto_now_add=True)

    class Meta:
        db_table = "regime_daily_reports"
        verbose_name = "每日日报"
        verbose_name_plural = "每日日报"
        ordering = ["-run_day", "symbol"]
        constraints = [
            # 一天一条。这个唯一键是「沉默可被识别」的前提：判断「今天有没有日报」
            # 就是一次按 (symbol, run_day) 的存在性查询，多出第二行会让它问错问题。
            # 写入方因此不新建第二行（同一天的心跳重复走到这里只确认已有那一行）。
            models.UniqueConstraint(
                fields=["symbol", "run_day"],
                name="uniq_daily_report_symbol_run_day",
            )
        ]

    def __str__(self) -> str:
        return (
            f"{self.symbol} {self.run_day} "
            f"{self.effective_regime or '（无结论）'}"
            f"{'｜判定缺失' if self.judgement_missing else ''}"
        )

    @property
    def regime_display(self) -> str:
        """生效阶段的中文名；无结论时给「（无结论）」而不是空串——日报若把空串印出来，
        读者看到的是一个没有内容的段落，而不是「今天没有结论」这句话。"""
        if not self.effective_regime:
            return "（无结论）"
        return BaseRegime(self.effective_regime).display

    @property
    def escalation_display(self) -> str:
        return Escalation(self.escalation).display if self.escalation else NO_ESCALATION_DISPLAY
