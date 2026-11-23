"""每日行情阶段判定（第①段单元 4）：三值落库 + `effective_at` 查询语义。

## 一天里发生的事

运行日 D（北京时间）的**判定时刻**（默认当晚 23:30，`config.JUDGEMENT_LIFECYCLE`），
机制取**已收盘的最后一条日线**（签署日 D−1），跑一次纯量化判定，把结论落成一条
`RegimeJudgement`，让它从北京时间 D+1 08:00 起生效。于是今天出的结论明天才咬人，
中间有一整段可见期——异常在生效前就能被拦下（CONTEXT.md）。

四个时刻的关系由本模块一处实现，模型上的 `run_day` / `run_at` 只是它的派生展示：

    业务日 D ── 判定时刻 D 23:30 ── 签署日 D−1（判定依据的日线）── 生效时刻 D+1 08:00

**业务日界（北京 08:00）与判定时刻是两个不同的东西，不要合成一个常量。** 日界必须留在
08:00，因为那是 UTC 00:00、是日线换线的那一刻（`apps/common/time_utils.BUSINESS_DAY_START_HOUR`
钉住，`test_timing.py` 守着）；而判定时刻只决定「日界之后要等到几点才判」，把它俩绑在
一起的话，想挪判定时刻就必然连带挪动日线对齐。两者分开之后，`attribute_date`（签署日）
与 `effective_at`（生效时刻）都只由 `run_day` 和日线决定，**与判定时刻无关**。

在业务日 D 的日界（08:00）到判定时刻（23:30）之间，心跳照跑但**不出结论**：它回一个
`skipped="before_judgement_moment"` 的收场。那段时间里「此刻生效中的」仍是 D−1 那条
（它的 `effective_at` 是 D 08:00），而 D 的结论要等到 23:30 才产出、D+1 08:00 才咬人。

## 「待生效」是查询语义，不是状态机

`current_judgement()` 取「`effective_at <= now` 的最新一条」即为当前生效态，
`pending_judgement()` 取「`effective_at > now` 的最早一条」即为待生效态。**不做**「次日
再跑一个任务把结论落成生效态」——那会凭空多一个定时任务和一个失败点，而那个任务挂了
结论就永远不生效且无人知道（CONTEXT.md 决策）。

两个查询条件里比的是 `effective_at`（归因时刻），**不是** `attribute_date`。拿 DateField
（会被补成当日零点）去比一个在日中间的时刻，问出来的就成了「今天这条结论生效了吗」，
而不是「此刻有没有任何结论生效」——于是每天 08:00 之后的每一次查询都会报「没有生效
中的阶段」。

## 判不出来就不落记录

量化判不出来（预热未满、分位窗口没攒满、ATR 恒为 0、日线还没到签署日）时**不写记录**，
而不是写一条「保守」或「沿用」的记录：`effective_at` 是运行日的函数，凭空多一条就等于
伪造了一次「今天得出过结论」。保持上一有效状态由查询语义天然承担——没有新记录，
`current_judgement()` 自然还指向上一条。日报第①段要明写「今日判定缺失」+ 原因，
这也正是 CONTEXT.md 给日报定的措辞。

数据不足（判不出来）与取数/代码故障在这里是**两类**：前者返回带原因的 `skipped` 结果，
后者往上抛。把数据不足抛成异常会让「预热没走完」这个已知的、有限的状态天天冒充故障告警，
二百多天以后没人再看它；把故障吞成 `skipped` 则会让判定静默死掉。

## 调度：搭在已有的 5 分钟心跳上（**对 CONTEXT.md 字面要求的一处偏离**）

CONTEXT.md 写的是「调度用 beat 的 crontab，且业务时区在 crontab 里显式构造」。本实现改成
由 `apps.trading.tasks.snapshot_daily_equity` 这条既有的 5 分钟幂等任务顺带调用，理由：

1. 本项目 `TIME_ZONE` 与 `CELERY_TIMEZONE` 都是 UTC（刻意保持，见 base.py 注释），
   北京 08:00 就是 UTC 00:00，一条 `crontab(hour=0)` 已经在语义上等于「北京 08:00」；
   「显式构造业务时区」在当前配置下不改变行为，只是对将来改 `TIME_ZONE` 的保险。
2. beat 用的是 `DatabaseScheduler`：`celery_app.py` 里的 `beat_schedule` 只是**播种**，
   DB 里的 `PeriodicTask`/`CrontabSchedule` 行才是活的事实，且启动时 `update_or_create`
   会按名字把同名行**覆写**回去。往这套东西里加条目或改时区，动的是正在跑的调度面。
3. 5 分钟心跳本身就满足「到点出结论」：判定时刻（默认北京 23:30）之后第一个 tick 就会写
   当天记录，`(symbol, effective_at)` 的唯一约束让它天然幂等，且失败会在 5 分钟后自动
   重试——不需要「必须在那一刻准确跑」这种脆弱前提，也就没有「beat 没起来 ⇒ 结论永不
   产生」的单点。「到点」由本模块自己判（`judgement_moment`），不由调度面判。
4. 判定记录自己带着运行日，所以「今天到底出了结论没有」由数据回答，不由「beat 有没有
   触发」推断。

**两条职责的先后是刻意的：先快照，后判定。** 两边失败都往上抛（真故障必须被任务健康
检查看见），所以排在后面的那条会被跳过这一轮——顺序问题于是变成「谁可以被打断一轮」。
判定坏了只损失一轮结论（5 分钟后再来，写入本来幂等），而快照坏了正是 CONTEXT.md 第
142 条那个「期初净值缺失 ⇒ 拒绝下单且不告警」的**静默**故障。让新机制有能力打断它，
是拿一个已知的静默故障去换一个不存在的收益。

**这处偏离已确认为有意为之**，不是可以随手推翻的文档措辞，也不是「实现时忘了」。
它换来的是：判定没有自己的调度面要维护，也就没有「beat 没起来 ⇒ 结论永不产生」的
单点；代价是判定跟着别人的心跳走，心跳停了判定也停。**这个代价今天只被看见一半**：
体检页的调度表那一段（`beat_health.py`）能说出「beat 没把那条心跳发出去」，说不出「发出去
了但每次都失败」——两条职责共用一个返回值，而那个返回值今天没有读者。完整的来龙去脉见
`tasks.py` 模块 docstring 的「跑了没」那一节。

要改回独立 crontab 条目时，改动面只有两处：本模块的 `run_daily_judgement()` 调用方，
以及 `celery_app.py` 的 `beat_schedule`（用**新名字**，不要改名或复用既有条目——复用
会让 `update_or_create` 把一条正在跑的调度覆写掉）。

## 两根轴

量化 → `base_regime`，资讯 → `escalation`，`effective_regime` 是两者合成。资讯只能把
结果推向更保守方向（`apply_escalation`，v1 只有「抬升到高波动」一种），这条在这里就用
一个断言钉住，而不是等单元 5 接资讯时再靠自觉。资讯抬升**不受最小持续期约束**（它按日
生效），但同样次日 08:00 才生效——即它只改 `effective_regime`，不参与 `base_regime`
的切换判定。

## 资讯轴从哪里接进来（单元 5iv）

资讯通道（`news_verdict.run_news_judgement`）在**量化判定之后、`_record_once` 之前**跑：

- 在量化之后，是因为判不出量化的日子（预热未满、日线还没到签署日）连记录都不会有，
  此时跑一轮资讯采集等于白花一次 LLM 调用；
- 在落库之前，是因为记录是事件、不更新，反了就是静默丢掉抬升标志。

**一天只跑一次**由「当天记录是否已存在」这道闸决定，不另设运行时刻配置——判定本身搭在
5 分钟心跳上，没有这道闸就是每 5 分钟一次真实调用。已有记录时整条通道跳过，返回值改成
**读库**（`_describe`），而不是拿本次重算的中间量汇报：通道没跑，重算出来的 `escalation`
恒为空，照它汇报就等于谎报「今天没有抬升」，而那正是日报第①段要读的字段。

通道失败（`status="failed"`）不抛：退回纯量化，`escalation` 为空，`news_ref` 里写明是
哪一段掉的。**不沿用昨日的资讯结论**——资讯抬升是唯一不受最小持续期约束的路径，拿
「保持上一有效状态」去兜它，会让「连着三天判不出来」看起来像「连着三天判过、结论是
不抬」。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.common.time_utils import business_day
from apps.regime import config
from apps.regime.candles import latest_complete_date
from apps.regime.models import (
    Escalation,
    RegimeJudgement,
    business_midnight,
    judgement_moment,
)
from apps.regime.news_verdict import run_news_judgement
from apps.regime.quant import PRIORITY, BaseRegime, latest_label

logger = logging.getLogger(__name__)

#: 默认标的。判定源全市场唯一（BTC），按用户分叉会得出互相矛盾的结论。
SYMBOL = config.CANDLES.symbol

_DECISION_ADOPTED = "adopted"
_DECISION_CARRIED = "carried"
_REASON_MIN_DWELL = "min_dwell"

#: `apply_min_dwell` 采纳一个**不等持续期**的候选时的留痕值，写进
#: `evidence["decision"]["dwell_exempt"]`。今天只有一个取值：保命档。
#: 它不复用 `reason`：`reason` 的语义是「为什么最后用的不是量化判出的那个」，
#: 而豁免时用的**正是**量化判出的那个，两者在日报里会写出互相矛盾的句子。
_EXEMPT_HIGH_VOL = "high_vol"


# --------------------------------------------------------------------------- #
# 查询语义
# --------------------------------------------------------------------------- #


def _records(symbol: str):
    return RegimeJudgement.objects.filter(symbol=symbol)


def current_judgement(
    symbol: str = SYMBOL, now: datetime | None = None
) -> RegimeJudgement | None:
    """**生效中**的判定：`effective_at <= now` 的最新一条。

    没有生效中的记录（冷启动，或从未判出来过）返回 `None`——调用方要区分「没有结论」
    与「结论是箱体震荡」，所以这里不给默认值。
    """
    now = now or timezone.now()
    return (
        _records(symbol).filter(effective_at__lte=now).order_by("-effective_at").first()
    )


def in_force_days(
    start: date, end: date, symbol: str = SYMBOL
) -> dict[date, tuple[BaseRegime, BaseRegime]]:
    """`[start, end]` 里**每一天在生效的那条判定**，值 = `(基础阶段, 生效阶段)`。

    「在生效」用的是与 `current_judgement` **同一句话**：`effective_at <= 业务日界(那天)`
    的最新一条。判据写了两遍（那边按时刻查一条、这边一次走完整段），所以 `test_judgement`
    里有一条用例逐日拿两边对账——两处分家的话，同一天会有两个「在生效」的答案，而那种
    错位处处自洽。

    一次查询走完整段，而不是每天调一次 `current_judgement`：一段人工真值可以横跨几个月，
    逐日查就是几百次查询，而这条读法每次都发生在体检页打开的那一刻。

    **没有生效判定的日子不进映射**（冷启动之前、判定链还没开始跑）：那不是「那天判成了
    箱体震荡」，是「那天没有判定」，调用方必须能分开。所以缺席 ≠ 某个默认档位。

    调用方是成功标准①（`truth.tally` 的 `in_force` 入参）：它靠这里的第二个值把**资讯
    抬升日**挑出去——那几天机制用的不是纯量化结论，拿纯量化标签去比是不公平的。
    """
    rows = list(
        _records(symbol)
        .filter(effective_at__lte=business_midnight(end))
        .order_by("effective_at")
        .values_list("effective_at", "base_regime", "effective_regime")
    )
    days: dict[date, tuple[BaseRegime, BaseRegime]] = {}
    index = 0
    in_force: tuple[datetime, str, str] | None = None
    day = start
    while day <= end:
        boundary = business_midnight(day)
        while index < len(rows) and rows[index][0] <= boundary:
            in_force = rows[index]
            index += 1
        if in_force is not None:
            days[day] = (BaseRegime(in_force[1]), BaseRegime(in_force[2]))
        day += timedelta(days=1)
    return days


def pending_judgement(
    symbol: str = SYMBOL, now: datetime | None = None
) -> RegimeJudgement | None:
    """**待生效**的判定：`effective_at > now` 的最早一条。

    取最早而不是最新：正常情况下最多只有一条待生效；真出现多条（比如任务停了几天又
    恢复）时，先说出口的是最先咬人的那条。
    """
    now = now or timezone.now()
    return (
        _records(symbol).filter(effective_at__gt=now).order_by("effective_at").first()
    )


def last_judgement(symbol: str = SYMBOL) -> RegimeJudgement | None:
    """**最近写下的**那条判定，不论它生效了没有。从未写成功过返回 `None`。

    这是日报第④段「上次判定成功时间」的唯一出处。记录**只在判定成功时**才落行
    （数据不足走 `skipped`、取数或代码故障往上抛，两种都不写记录），所以「最近一条
    记录的写入时刻」就是「上次判定成功的时刻」。

    **不能拿 `current_judgement().created_at` 顶替**：今天的判定要到签署日 +2 天的北京
    08:00 才生效，那之前 `current_judgement()` 返回的还是昨天那条，于是一个**完全健康**
    的通道会被日报说成「上次成功是昨天」——而「上次成功是昨天」在机制健康那一段里读
    起来就是「通道出问题了」。两个问题两种读法，这里刻意各留一个函数。

    不跟时钟打交道：`created_at` 是写入时刻，与「现在几点」无关，所以不收 `now`。多一个
    用不上的参数只会让人以为这条读法有时效语义。`-id` 只是并列时的定序（同一微秒写进
    两条的概率约为零），取哪条不影响时刻，但影响调用方从返回行上读到的阶段，所以不留给
    数据库自己挑。
    """
    return _records(symbol).order_by("-created_at", "-id").first()


def in_force_since(record: RegimeJudgement) -> datetime:
    """`record.effective_regime` 这个阶段是从哪一刻开始生效的。

    以「按 `effective_at` 倒序回溯，直到生效阶段变样的那一条」为界。中途**没有记录**
    的日子不打断这一段：那些天判定缺失、状态按查询语义保持原样，阶段并没有变过。
    """
    start = record.effective_at
    regime = record.effective_regime
    rows = (
        _records(record.symbol)
        .filter(effective_at__lte=record.effective_at)
        .order_by("-effective_at")
        .values_list("effective_at", "effective_regime")
    )
    for effective_at, value in rows:
        if value != regime:
            break
        start = effective_at
    return start


# --------------------------------------------------------------------------- #
# 两根轴合成
# --------------------------------------------------------------------------- #


def apply_escalation(base: BaseRegime, escalation: str) -> BaseRegime:
    """把基础阶段按抬升标志合成出生效阶段。

    两条规则，都在这里一次性钉住（单元 5 接资讯时不应有第二处实现）：

    1. **只能更保守。** 合成结果在 `PRIORITY` 里的位次不得低于基础阶段——「LLM 只能让
       系统更保守，永远不能让它更激进」是这份设计的硬约束，而资讯就是 LLM 产出的。
       这里用位次比较而不是 `>`：`DOWNTREND` 与 `RANGE` 之间没有大小关系，一个
       「生效阶段比基础阶段更激进」的合成结果必须是被断言拦下的 bug，而不是被静默采纳。
    2. **v1 只有一种抬升：抬到高波动。** 资讯不沿优先级阶梯逐级调整，因为「资讯说该
       谨慎到什么程度」没有可标定的刻度，而「抬到高波动」这一步已经足以停掉全场策略。
       基础阶段已经是高波动时抬升是空操作，但标志**照记**——它是资讯那根轴的原始输出，
       不可重放，不能因为「恰好没有改变结果」就丢掉。

    只返回合成的生效阶段；抬升标志由调用方原样落库，不在这里做归一化——标志是资讯轴
    的原始输出，归一化会让「资讯说了什么」与「库里记了什么」出现第二处口径。
    """
    escalation = escalation or ""
    if not escalation:
        return base
    if escalation != Escalation.NEWS.value:
        raise ValueError(f"未知的抬升标志：{escalation!r}")
    effective = BaseRegime.HIGH_VOL
    if PRIORITY.index(effective) > PRIORITY.index(base):
        raise AssertionError(
            f"合成结果比基础阶段更激进：{base.value} → {effective.value}——"
            "资讯只能让系统更保守"
        )
    return effective


def apply_min_dwell(
    candidate: BaseRegime,
    candidate_effective_at: datetime,
    current: RegimeJudgement | None,
    min_dwell_days: int,
) -> tuple[BaseRegime, str, dict]:
    """最小持续期：阶段是慢变量，切换要等当前阶段站稳 `min_dwell_days` 个自然日。

    返回 `(采用的基础阶段, 原因, 说明数字)`。被拦下时**沿用当前的量化阶段**并说明原因——
    拦下不等于不记录：今天确实得出了结论，结论内容是「维持不变，因为持续期不够」，
    这是一条该被 Shadow 看到的信息，不是缺失。

    ## 约束的是**量化轴**，不是资讯轴

    这条规则的比较对象是 `base_regime`（纯量化口径），返回值也写进 `base_regime`。三条
    理由，任何一条单独成立就够：

    1. CONTEXT.md 第 104 条：**资讯抬升不受最小持续期约束、按日生效**，理由写着「若要求
       抬升自身维持 `min_dwell_days` 天，会出现『第 2 天资讯已撤回、抬升却撤不掉』的
       怪状态」。拿 `effective_regime` 当尺子，抬升立刻把生效阶段变成高波动，而持续期
       量的正是这个阶段——于是抬升**事实上**被锁了 `min_dwell_days` 天，那一条规则被
       实现悄悄取消了。
    2. 三值留痕（基础 / 抬升 / 生效）存在的唯一目的是回答「今天这个阶段是量化判出来的，
       还是资讯抬上来的」。把 `effective_regime` 写进 `base_regime`，这一列就在撒谎：
       量化明明判的是箱体震荡，库里记着高波动，归因只能靠翻 `evidence` JSON。
    3. 持续期防的是「量化在阈值附近来回抖」。资讯轴是单向的、且按日生效，它没有「抖」
       可言，拿一把防抖的尺子去量它，量的是一个不存在的属性。

    `in_force_days` 是「候选结论生效的那一刻，当前量化阶段已经站了几天」，也就是本规则
    实际比较的那个量——它与 `min_dwell_days` 同尺度，读日志时不必再做换算。比较的是绝对
    时刻之差而不是自然日序号：两端的 `effective_at` 都是业务日界（北京 08:00），差必定是
    24 小时的整数倍，所以这里不会出现「差 4 天 23 小时算几天」的问题。

    冷启动（`current is None`）直接采纳：没有「当前阶段」可站，持续期无从谈起。CONTEXT.md
    的「冷启动默认不执行任何停用」是停用决策那一侧的规则，不是这里——首条判定必须落下来，
    否则查询语义永远返回 `None`，整个机制卡在起点。

    ## 保命档（`HIGH_VOL`）不受持续期约束

    候选是 `HIGH_VOL` 时**直接采纳**，不等 `min_dwell_days`。这与资讯抬升那条豁免同源，
    理由却更硬：

    1. 持续期是**防抖**的尺子，而它默认「两个方向对称地错」——早切一天与晚切一天代价相当。
       保命档没有这个对称性：它管的是「此刻谁都不该开新仓」。早到一天是少赚，晚到一天是
       在极端行情里继续加敞口。**这一个方向的不对称足以推翻防抖的正当性**。
    2. CONTEXT.md 第 128 条把高波动定为「叠加在当前状态上的保命层」，与事件熔断同属
       `halt` 的触发源而非适用性结论；事件熔断按窗口即时生效、从不看持续期。让同一个
       语义在两个触发源上要求不同的等待天数，等于让「保命」这个名字在一条路径上是假的。
    3. 量化的 `HIGH_VOL` 判据本身已经是一道**慢闸**：ATR% 分位要越过第 80 百分位、且窗口
       是 250 天。它天然滞后于行情，再叠一道防抖闸是拿两把尺子量同一件事。
    4. 实测代价（2026-11-16，txy 生产，当时 `min_dwell_days` 还是 5）：11-14 的
       `atr_pct_rank` 已达 0.916、量化判 `high_vol`，而落库仍是 `range`，`in_force_days`
       只走到 3——**保命档被自己的防抖闸拦在门外 4 天**，同期 BTC 从 80476 跌到 65351
       （−18.8%）。这正是 `config.py` 里「它恰恰在该保命的日子里说不必保命」那句注释
       预言的情形。**同一个失效还有另一半**：那把闸本身也偏长，2026-11-16 已由 5 收到 3。

    **豁免只针对 `HIGH_VOL` 候选，不对其它三档开口子**：`DOWNTREND` / `UPTREND` / `RANGE`
    之间的切换照旧等满 `min_dwell_days`。这是「保命档单向」的又一处落地——它只能让系统
    更早进入风险状态，不能让任何档位更早离开。

    豁免被采纳时 `numbers` 里带 `dwell_exempt: "high_vol"`，`action` 仍是 `adopted`、
    `reason` 仍为空：豁免不是一次「被拦下」，把它记成 `carried` 会让日报第①段写出
    「维持不变：持续期不足」这句与事实相反的话。留痕靠那个键，不靠 reason。
    """
    if current is None:
        return candidate, "", {"in_force_days": None, "carried_from_effective_at": None}

    start = base_regime_since(current)
    in_force_days = (candidate_effective_at - start).days
    numbers = {"in_force_days": in_force_days, "carried_from_effective_at": None}

    if candidate is _base_regime_of(current):
        return candidate, "", numbers

    if candidate is BaseRegime.HIGH_VOL:
        # 保命档：不设防抖闸。见 docstring「保命档（HIGH_VOL）不受持续期约束」。
        return candidate, "", {**numbers, "dwell_exempt": _EXEMPT_HIGH_VOL}

    if in_force_days >= min_dwell_days:
        return candidate, "", numbers

    return (
        _base_regime_of(current),
        _REASON_MIN_DWELL,
        {
            "in_force_days": in_force_days,
            "carried_from_effective_at": current.effective_at.isoformat(),
        },
    )


def _base_regime_of(record: RegimeJudgement) -> BaseRegime:
    """记录上的**基础阶段**（纯量化口径）还原成枚举。

    取 `base_regime` 而不是 `effective_regime`：持续期约束的是量化判定的抖动，而
    `effective_regime` 混进了资讯那根轴。完整理由见 `apply_min_dwell` 的 docstring。

    **不要再加一个 `_regime_of` 读 `effective_regime` 的兄弟函数给这里用**：那样
    「这条规则比的是哪根轴」又只剩 docstring 记得，而这次修的正是那个错误
    （拿 `effective_regime` 当尺子 → 资讯抬升借走 `min_dwell_days` 天免疫期）。
    """
    return BaseRegime(record.base_regime)


def base_regime_since(record: RegimeJudgement) -> datetime:
    """`record.base_regime` 这个**量化阶段**是从哪一刻开始站住的。

    与 `in_force_since` 的区别只有一列：那个按 `effective_regime` 回溯（资讯抬升会打断
    它），这个按 `base_regime` 回溯（资讯抬升**不**打断它）。持续期要的是后者——它问的
    是「量化已经在这个档位上待了几天」，而资讯那根轴自己按日生效，与持续期无关。

    中途**没有记录**的日子同样不打断：那些天判定缺失、状态按查询语义保持原样，阶段并没有
    变过。
    """
    start = record.effective_at
    regime = record.base_regime
    rows = (
        _records(record.symbol)
        .filter(effective_at__lte=record.effective_at)
        .order_by("-effective_at")
        .values_list("effective_at", "base_regime")
    )
    for effective_at, value in rows:
        if value != regime:
            break
        start = effective_at
    return start


# --------------------------------------------------------------------------- #
# 每日判定
# --------------------------------------------------------------------------- #


def load_candles(symbol: str = SYMBOL) -> list[dict]:
    """取该标的的**全部**已收盘日线，按日期升序。

    全量而不是「最后 250 根」：EMA 是带无限记忆的递推量，喂进去的窗口从哪里开始会
    改变它今天的值。截断取数会让「实时判定」与「单元 6 拿全量历史逐日标注」对同一天
    得出不同的阶段，而那种差异看起来完全正常。日线一年 365 行，全量读的代价可以忽略。
    """
    from apps.regime.models import DailyCandle

    return list(
        DailyCandle.objects.filter(symbol=symbol)
        .order_by("date")
        .values("date", "high", "low", "close")
    )


def build_evidence(label, decision: dict) -> dict:
    """判定记录里的依据数字：量化原始输出 + 从输出到基础阶段之间发生的事。

    两组分开的理由是**它们回答的是两个问题**：「量化看到了什么」（原始输出，未经任何
    规则改写，含 `regime=None` 这种判不出来的情形）与「为什么最后用的不是它」（被持续期
    拦下、沿用了哪条记录、已持续几天）。混成一组之后，「今天的基础阶段为什么是箱体震荡」
    就需要读者自己去推。
    """
    return {
        "quant": {
            "regime": label.regime.value if label.regime else None,
            "atr": label.atr,
            "atr_pct": label.atr_pct,
            "atr_pct_rank": label.atr_pct_rank,
            "quantile_sample": label.quantile_sample,
            "ema_fast": label.ema_fast,
            "ema_slow": label.ema_slow,
            "ema_slope": label.ema_slope,
            "separation": label.separation,
        },
        "decision": decision,
    }


def run_daily_judgement(
    symbol: str = SYMBOL,
    now: datetime | None = None,
    escalation: str | None = None,
    news_ref: dict | None = None,
) -> dict:
    """跑一次当日判定并按需落库。返回值进日志与调用方（任务）的返回值，不静默。

    `escalation=None` 是**生产路径**：自己去跑一轮资讯通道（`run_news_judgement`），用
    它给出的抬升标志与引用快照。给了值（`""` 或 `Escalation.NEWS.value`）就是**覆盖**，
    通道不跑——这是测试与数据订正用的口子，不是第二条生产路径。

    **同一个运行日只会写一条记录**：`(symbol, effective_at)` 上有唯一约束，写入走
    `get_or_create`。心跳每 5 分钟一次，一天里绝大多数 tick 都会走到这里，因此这条路
    必须便宜且无副作用——判不出来时它只读不写，已判过时连资讯通道都不跑。

    ## `run_day` 是**业务日**，不是业务时区的日历日

    这两者差着业务日的日界（北京 08:00 == UTC 00:00 == 日线换线），而北京时间
    00:00–08:00 那 8 小时正是两者错开的地方。取日历日的后果不是「偶尔早一天」，是
    **每一天的签署日都比应得的慢一天，且永不自愈**：

    - 心跳在北京 00:03（UTC 16:03）跑，`to_business(now).date()` 已经翻到新的一天；
    - 但那一刻最新已收盘的日线还停在**前天**（今天的日线要到 UTC 00:00 才收盘）；
    - 于是 `attribute_date`（来自日线）与 `run_day`（来自墙上时钟）相差 2 天而不是 1 天；
    - `effective_at` 是 `run_day + 1` 的函数，`get_or_create` 又**永不更新**——北京 08:00
      日线换线之后，运行日对应的那条记录已经被 00:03 那个 tick 写死了，这一天的结论
      永久地晚一天。实测生产库 32 条记录**全部** `effective_at − attribute_date = 3 天`，
      而契约是 2 天。

    改用 `business_day()`（先换算业务时区、再看是否已过日界）之后，00:03 那个 tick
    归属的是**昨天**这个业务日——它与「此刻已收盘的日线是前天」是一致的，于是映射回到
    契约的 2 天。08:00 之后的 tick 归属新业务日，那时新日线也已收盘，同样一致。

    这不改变「同一业务日只写一条」的幂等性：`business_day()` 是 `now` 的分段常函数，
    分段点正是日界，而日界前后 `attribute_date` 也恰好一起变。
    """
    now = now or timezone.now()
    run_day = business_day(now)
    effective_at = business_midnight(run_day + timedelta(days=1))

    existing = _find_record(symbol, effective_at)
    if existing is not None:
        # 今天已经判过了——心跳一天里绝大多数 tick 走这条路。**资讯通道不能重跑**：
        # 记录是事件、不更新，重跑只是把同一批条目再投一次 LLM，而那些结论永远没有
        # 落库的机会。
        #
        # **这一支排在所有别的东西之前，包括日线库与判定时刻闸。** 两条理由：
        #
        # 1. 「今天已经出过结论」是比「今天还没到点」更强的事实。顺序反过来时，任何
        #    把判定时刻往后调的改法（包括本次改动当天）都会让一条**已经写下的**结论在
        #    当天剩下的时间里被报成「还没到判定时刻」——而日报与 Shadow 都按返回值走，
        #    它们会跟着说「今天没有结论」，尽管库里躺着一条。
        # 2. 它也是这里唯一**只需要 run_day** 的一支：日线要全量读一遍、标签要整条重算，
        #    而它们的结果在这一支里一个都用不上。心跳每 5 分钟一轮，省下的是每一次 tick
        #    的 2500 根日线读取与全序列指标重算。
        return _describe(existing, run_day)

    moment = judgement_moment(run_day)
    if now < moment:
        # 业务日 D 的日界（北京 08:00）已过，但还没到 D 的判定时刻（默认当晚 23:30）。
        # 这段时间**有意不出结论**：判定时刻是可调的日频节奏，而「日界一过就判」是旧行为。
        return {
            "skipped": "before_judgement_moment",
            "symbol": symbol,
            "run_day": run_day.isoformat(),
            "judgement_at": moment.isoformat(),
        }

    candles = load_candles(symbol)
    if not candles:
        logger.warning("[regime] %s 没有任何日线，无法判定", symbol)
        return {
            "skipped": "no_candles",
            "symbol": symbol,
            "run_day": run_day.isoformat(),
        }

    attribute_date: date = candles[-1]["date"]
    expected = latest_complete_date(now)
    if attribute_date != expected:
        # 日线还没同步到今天该有的那一根：此时判定会「用前天的行情签署今天的结论」，
        # 而签署日正是单元 6 切片时的 join 键。宁可今天没有结论。
        logger.warning(
            "[regime] %s 最新日线是 %s，但此刻应已收盘的是 %s，跳过判定",
            symbol,
            attribute_date,
            expected,
        )
        return {
            "skipped": "stale_candles",
            "symbol": symbol,
            "run_day": run_day.isoformat(),
            "latest_candle": attribute_date.isoformat(),
            "expected_candle": expected.isoformat(),
        }

    label = latest_label(candles)
    if label is None or not label.judged:
        reason = "undecidable" if label is not None else "empty_series"
        logger.info(
            "[regime] %s %s 判不出来（%s），保持上一有效状态",
            symbol,
            attribute_date,
            reason,
        )
        return {
            "skipped": reason,
            "symbol": symbol,
            "run_day": run_day.isoformat(),
            "attribute_date": attribute_date.isoformat(),
            "atr_pct_rank": None if label is None else label.atr_pct_rank,
        }

    if escalation is None:
        outcome = run_news_judgement(symbol, now)
        escalation = outcome.escalation
        news_ref = outcome.ref

    current = current_judgement(symbol, now=now)
    base, reason, numbers = apply_min_dwell(
        label.regime,
        effective_at,
        current,
        config.JUDGEMENT_LIFECYCLE.min_dwell_days,
    )
    effective_regime = apply_escalation(base, escalation)

    record, created = _record_once(
        symbol=symbol,
        attribute_date=attribute_date,
        effective_at=effective_at,
        base_regime=base,
        escalation=escalation or "",
        effective_regime=effective_regime,
        evidence=build_evidence(
            label,
            {
                "action": _DECISION_CARRIED if reason else _DECISION_ADOPTED,
                "reason": reason,
                **numbers,
            },
        ),
        news_ref=news_ref,
    )

    result = {
        "symbol": symbol,
        "run_day": run_day.isoformat(),
        "attribute_date": attribute_date.isoformat(),
        "effective_at": effective_at.isoformat(),
        "base_regime": base.value,
        "escalation": escalation or "",
        "effective_regime": effective_regime.value,
        "decision": _DECISION_CARRIED if reason else _DECISION_ADOPTED,
        "reason": reason,
        "recorded": created,
    }
    if created:
        logger.info("[regime] 判定落库 %s", result)
    return result


def _find_record(symbol: str, effective_at: datetime) -> RegimeJudgement | None:
    """今天这条记录是否已经写过了。查询键与 `_record_once` 的唯一约束是同一对。"""
    return _records(symbol).filter(effective_at=effective_at).first()


def _describe(record: RegimeJudgement, run_day: date) -> dict:
    """把一条**已有**记录读成 `run_daily_judgement` 的返回值。

    读库而不是拿本次重算的中间量汇报：走到这条路时资讯通道根本没跑，重算出来的
    `escalation` 恒为空，照它汇报就等于谎报「今天没有抬升」——而那正是日报第①段
    要读的字段。`decision` 与 `reason` 同理，从 `evidence` 里读回落库时的原话。

    `recorded` 恒为 `False`：本次没有新建记录。调用方据此区分「今天刚出结论」与
    「今天早就有结论，这只是心跳又跑了一遍」。
    """
    decision = (record.evidence or {}).get("decision") or {}
    return {
        "symbol": record.symbol,
        "run_day": run_day.isoformat(),
        "attribute_date": record.attribute_date.isoformat(),
        "effective_at": record.effective_at.isoformat(),
        "base_regime": record.base_regime,
        "escalation": record.escalation,
        "effective_regime": record.effective_regime,
        "decision": decision.get("action", ""),
        "reason": decision.get("reason", ""),
        "recorded": False,
    }


def _record_once(**fields) -> tuple[RegimeJudgement, bool]:
    """按 `(symbol, effective_at)` 写且只写一次，返回 `(记录, 是否新建)`。

    已存在时**不更新**：记录是事件，不是缓存。事后有人正在读那条记录、或单元 6 已经
    按它切过片，此时把它重写一遍就成了改历史。真需要纠正，走数据订正而不是让心跳顺手改。
    """
    with transaction.atomic():
        try:
            return RegimeJudgement.objects.get_or_create(
                symbol=fields["symbol"],
                effective_at=fields["effective_at"],
                defaults={
                    "attribute_date": fields["attribute_date"],
                    "base_regime": fields["base_regime"].value,
                    "escalation": fields["escalation"],
                    "effective_regime": fields["effective_regime"].value,
                    "evidence": fields["evidence"],
                    "config_snapshot": config.full_snapshot(),
                    "news_ref": fields["news_ref"],
                },
            )
        except Exception:
            # 与并发 tick 撞上唯一约束时 `get_or_create` 自己会回查一次；走到这里是
            # 真的写不进去（约束之外的 DB 故障），必须往上抛——让任务标 FAILURE，
            # 而不是把故障吞成一行日志。
            logger.error("[regime] 判定记录写入失败", exc_info=True)
            raise
