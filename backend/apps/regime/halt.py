"""halt 状态机——「停止」的唯一抽象（ADR 0001）。

ADR 0001 把停止的语义从「谁在拦」改成了「谁投的触发源」。所以这个模块只做一件事：
**把一组声明求值成「此刻挡不挡得住这一张单」**，并说清是哪个源、什么时候、依据什么。
日内亏损、连续下单失败、API 断连、行情阶段切换、事件熔断——全部是往
``HaltDeclaration`` 投递的触发源，谁都不许自己造第二套停止抽象。

三条已经定死的形状，都落在这个模块的接口上：

1. **不是一行布尔，是一组声明**，唯一键为（触发源 × 作用域），每行带自己的生效期
   （CONTEXT.md:130）。「当前是否拦」= 存在任一命中的生效行。所以这里返回的是
   ``HaltVerdict.layers``（一个集合，不是一条），与 ``query_halt`` 的「两层可以同时
   生效」（CONTEXT.md:177）是同一件事。
2. **判定函数只读状态，不读事件表**（CONTEXT.md:134）。事件表 → 状态行的搬运由窗口
   同步任务（`apps/regime/halt_sync.py`，每 300 秒整表重算一轮）完成；本模块**不 import**
   ``MajorEvent``，也不 import ``deactivation_run``。它只读 ``HaltDeclaration`` 与
   ``RegimeMechanismSwitch`` 这两张状态表。这条是刻意的：判定函数一旦自己去读事件表，
   就有了第二个求值口径，而两个口径的分歧只会出现在「任务没跑成」的时候——那时没人
   在看。
3. **持久化，扛过进程重启**（CONTEXT.md:132）。所以状态在表里，本模块不做任何进程内
   缓存：缓存会让「刚投的声明」与「已解除的声明」各自延迟生效，而延迟窗口里的判定
   依据是一条已经不存在的声明。

**开关与声明是两件事，都必须为真才作数。** 声明说「这个源此刻想拦」，开关说「这个源
整体上是否启用」。两者分开的理由写在 ``HaltDeclaration`` 的 docstring 里（关掉开关时
那些行必须留着当记录）。映射是 ``HALT_TRIGGER_SWITCH``；映射到 ``None`` 的触发源没有
开关，永远作数——**保命档就是这一档**（「高波动算生效」，它是阶段本身的性质，不由任何
人确认才生效）。

**这一档是同步函数。** 它读数据库，而 ``RiskGuard.pre_trade_check`` 是 async，所以调用
方必须经 ``db_async`` / ``sync_to_async`` 进来（项目规则：绝不在 async 上下文里写裸的
同步数据库查询）。实现成同步而不是 async，是因为它还要被同步的日报与窗口同步任务调用，
而 async 函数在同步上下文里没法调——反过来则只是一个 ``sync_to_async`` 包一层。
"""

# --------------------------------------------------------------------------- #
# 本模块只读，写方是 `apps/regime/halt_sync.py`（第②c 段的窗口同步任务）。两件当初在这里
# 记下、现在已由写方落实的事，留在这里当「为什么写方是那样写的」：
#
# 1. `opened_at` 记的是**事实里已经存好的那个时刻**（事件的 `halt_at`、判定的
#    `effective_at`），不是任务跑起来的 `timezone.now()`。`HaltDeclaration` 刻意没有
#    「底层事件是谁」这一列，所以这张表答不出「这条声明对应事件表里的哪一行」；只要
#    `opened_at` 取自事实，`events.py` 那些按事件自己的时间标的指示灯（熔断窗口 =
#    `[business_midnight(run_day), business_midnight(run_day + 1))`）就还是同一个数。
#    记成 `now()` 的话它们会随任务延迟漂移，表现为「日报上说熔断了、事件表上说没到时间」。
# 2. 同一个（触发源 × 作用域）上重叠的多个事件**合并成一行**，`label` 与 `reason` 把
#    同时生效的事件都写进去。这不是风格问题，是唯一键（`uniq_live_halt_declaration`）
#    逼出来的：拆成多行的话第二次写入就撞唯一键，而「解除只终结自己那一行」
#    （CONTEXT.md:130）又不允许一次终结多行。
# --------------------------------------------------------------------------- #

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from django.db.models import Q
from django.utils import timezone

from apps.regime.models import (
    HaltDeclaration,
    HaltTrigger,
    MechanismKind,
    MechanismMode,
    RegimeMechanismSwitch,
)

# 作用域的三种写法。存成 `kind:value` 的字符串而不是三列，理由见 `HaltDeclaration`。
SCOPE_GLOBAL = "global"
_SYMBOL_PREFIX = "symbol:"
_STRATEGY_PREFIX = "strategy:"

# 触发源 → 管着它的开关；`None` = 没有开关，永远作数。
#
# **`BLANKET` 映射到 `None` 是刻意的**：行情阶段 gate 那个开关（`REGIME_GATE`）要到第③段
# 才接线，若把保命档挂在它下面，保命档就会被一个「默认关」的开关关掉——而保命档是
# 「高波动算生效」的，它不需要任何人确认。
#
# **`MANUAL` 也映射到 `None`，但理由相反**：保命档不需要确认就生效，手动熔断是**人当场
# 确认过才生效**的——触发方（`actor`）与原因已经留在声明行上，再套一层机制开关等于让
# 一个没参与这次决定的状态否决一个已经认领决定的人。Shadow 的「只记录、不真拦」约束的是
# **机制**的自动行为；manage.py manual_halt 是人在机制之外亲手按下的停止，不受它辖制。
#
# `DEACTIVATION` 指向第③段的开关：让「策略档的声明归哪个源」有唯一答案（Q3 的判据要用到
# 触发源），也免得第③段为了补一行映射又动一次迁移。
#
# `REGIME_SUSPENSION`（按阶段暂停开新仓，issue #51）**与 `DEACTIVATION` 共用 `REGIME_GATE`
# 这一个开关**：档位记的是「机制整体的自动动作」这一个数，两路都是这个档位下的动作。各自
# 再领一个开关的话，用户关一次 gate 只停了半个机制，而 `gate_switch` 的页面上写着「机制
# 已回 Shadow」——那句就成了假话。分开的是**声明行**（谁投的、解除条件不同），不是档位。
HALT_TRIGGER_SWITCH: dict[HaltTrigger, MechanismKind | None] = {
    HaltTrigger.BLANKET: None,
    HaltTrigger.EVENT: MechanismKind.EVENT_BREAKER,
    HaltTrigger.DEACTIVATION: MechanismKind.REGIME_GATE,
    HaltTrigger.REGIME_SUSPENSION: MechanismKind.REGIME_GATE,
    HaltTrigger.MANUAL: None,
}


# --------------------------------------------------------------------------- #
# 作用域
# --------------------------------------------------------------------------- #


def global_scope() -> str:
    """全市场作用域。保命档与「影响整体行情」的事件用它。"""
    return SCOPE_GLOBAL


def symbol_scope(symbol: str) -> str:
    """品种作用域。``symbol`` 必须与 ``OrderRequest.symbol`` 同一套写法。

    刻意**不做归一化**：``DOGE/USDT`` 与 ``DOGEUSDT`` 的换算由适配器负责，在这里再实现
    一次就是给「这个品种叫什么」造第二套答案，而两套答案分歧的表现是「声明写了却拦不住」
    ——一条看起来正常的记录。写入方与读取方重合（两边都是同一批调用点）会兜住这个风险。
    """
    return f"{_SYMBOL_PREFIX}{symbol}"


def strategy_scope(strategy_id) -> str:
    """策略作用域。第③段的停用决策用它（第②段只求值 global 与 symbol 两档）。"""
    return f"{_STRATEGY_PREFIX}{strategy_id}"


def parse_scope(scope: str) -> tuple[str, str | None]:
    """``'global'`` / ``'symbol:DOGE/USDT'`` / ``'strategy:<uuid>'`` → ``(档, 值)``。

    **认不出来的写法当成 ``global``**，而不是「不拦」：读不懂的作用域只能来自写入方的
    bug，而一条写坏了的声明静默失效是没有任何别的信号能暴露的——它看起来与「本来就没
    声明」一模一样。当成全市场会拦住所有开仓，吵闹但看得见。这与 Q2 对「查不到状态」
    的 fail-closed 是同一条取向。
    """
    text = (scope or "").strip()
    if text.startswith(_SYMBOL_PREFIX):
        return ("symbol", text[len(_SYMBOL_PREFIX) :])
    if text.startswith(_STRATEGY_PREFIX):
        return ("strategy", text[len(_STRATEGY_PREFIX) :])
    return ("global", None)


def scope_display(scope: str) -> str:
    """作用域的人话（日报、``query_halt`` 与拒绝理由共用，不许各写一句）。"""
    kind, value = parse_scope(scope)
    if kind == "symbol":
        return f"品种 {value}"
    if kind == "strategy":
        return f"策略 {value}"
    return "全市场（global）"


def trigger_of(row: HaltDeclaration) -> HaltTrigger:
    """行上的触发源枚举。存的是字符串（``choices`` 的取值），这里转回枚举。"""
    return HaltTrigger(row.trigger)


# --------------------------------------------------------------------------- #
# 求值
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HaltLayer:
    """一层在拦的声明（求值结果，不是数据库行）。

    刻意不直接返回 ``HaltDeclaration``：调用方需要的是「谁在拦」这四件事，不是一张表。
    返回行对象的话，每个调用方都会自己去 ``row.trigger`` / ``row.scope`` 里拼同一句话，
    而拼法一旦分叉，「同一件事在两处说法不同」就回来了。
    """

    trigger: HaltTrigger
    scope: str
    label: str
    reason: str
    opened_at: datetime
    expires_at: datetime | None

    @property
    def text(self) -> str:
        """一句话说清这一层是谁在拦。**作用域必须写进去**：「拦全场」与「只拦某个
        品种」在排查时是完全不同的两件事，而只写触发源时它们长得一样。"""
        return f"{self.label}（{self.trigger.display}，作用域 {scope_display(self.scope)}）"


@dataclass(frozen=True)
class HaltVerdict:
    """一次求值的全部结论。**层是一个集合**——几层可以同时生效（CONTEXT.md:177）。"""

    layers: tuple[HaltLayer, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.layers)

    @property
    def reason(self) -> str:
        """拒绝理由（空串 = 没有层在拦）。

        每层都写出来而不是只写第一层：同时生效时只报一层，会让人按报出来的那层去排查，
        而解除它之后单还是下不出去——那时会以为是别的问题。
        """
        if not self.layers:
            return ""
        return "停止判定命中：" + "；".join(layer.text for layer in self.layers)


def switch_mode(kind: MechanismKind) -> MechanismMode:
    """这一档现在是哪一档。

    **本函数是「此刻这条线开没开」这一类判断的唯一读口**：`switch_open`（halt 侧）与候选
    漏斗侧（`strategy_factory.orchestrator`）都从这里读，所以两侧不可能读到两个答案——
    而「同一档位下 halt 侧读、漏斗侧不读」正是票 #50 修掉的那个分叉。

    **别把它读成字面上的「全仓唯一调用点」**：仓里另有多处 `RegimeMechanismSwitch.current(...)`
    的调用（`gate_switch` / `mechanism_switch` / `breaker_switch` 的确认页与写路径幂等、
    `briefing` / `report` / `regime_queries` 的回显），它们各自答的是「页面上显示什么」
    或「这条命令要不要写一行流水」，**不答「此刻哪条线在拦」**。本函数的管辖范围是后者
    这一类判断，不是 `current()` 的所有调用方。

    **收 `kind` 而不是收触发源**：`switch_open` 手里是 `HaltTrigger`（要先按
    `HALT_TRIGGER_SWITCH` 换一次），而候选漏斗那一侧手里直接就是 `MechanismKind`。两个
    入口共用下面同一处读，而不是让漏斗侧自己去 `RegimeMechanismSwitch.current(...)`。
    """
    return RegimeMechanismSwitch.current(kind)


def is_open_mode(mode: MechanismMode) -> bool:
    """这个档位算「打开」吗。

    **本仓「哪一档算开」这条判据的唯一定义处**：`switch_open` 与候选漏斗侧都比对这一个
    函数的结果，谁都不自己写 `is MechanismMode.EXECUTING`——写第二遍的人，会在将来多一档
    （如「只拦订单不拦候选」）时安静地把它读成「关」，而它们各自看都是对的。

    同样**不是字面上的「全仓唯一比较点」**：`gate_switch` / `briefing` / `mechanism_switch`
    / `event_breaker` 另有几处 `is MechanismMode.EXECUTING`（确认页与写路径幂等、回显），
    那些比较答的是「这个页面/这条命令处在什么档」，不参与「哪条线在拦」的判定。
    """
    return mode is MechanismMode.EXECUTING


def switch_open(
    trigger: HaltTrigger, cache: dict[MechanismKind, bool] | None = None
) -> bool:
    """这个触发源的开关打开了没有。没有开关的触发源恒为真（保命档）。

    **这是「哪个开关管哪条线」的唯一换算口**，所以它是公开的：读侧（``blocking_declarations``
    ——「此刻挡不挡得住一张单」）与写侧（``reduce_run``——「此刻该不该真的对市场动手」）
    问的是同一个问题，各自去查一遍 ``HALT_TRIGGER_SWITCH`` 就是给这张表造第二个答案。
    而这张表里有**两处刻意的不对称**（``BLANKET → None`` 与 ``MANUAL → None``，见上面的
    注释），第二个答案
    分叉的表现就是保命档被某个「默认关」的开关关掉——那正是注释里说要避免的事。

    **「档位怎么读」与「哪一档算开」都不在本函数里**：它们分别是 `switch_mode` 与
    `is_open_mode`（各自唯一一处）。本函数只做「触发源 → `kind`」这一跳，再交给那两个。
    自己在这里写一遍 `is MechanismMode.EXECUTING` 就是给「哪一档算开」造第二个答案——
    而那正是候选漏斗接档位时（票 #50）被发现的分叉形态：同一档位下，halt 侧读、漏斗侧不读。

    ``cache`` 是可选的一次求值内共享缓存（按 ``kind`` 缓存）。**不要**在这里做模块级缓存：
    那一层会变成进程内的开关快照，见模块 docstring 第 3 条（声明与开关都必须现读）。
    """
    kind = HALT_TRIGGER_SWITCH.get(trigger)
    if kind is None:
        return True
    cache = {} if cache is None else cache
    if kind not in cache:
        cache[kind] = is_open_mode(switch_mode(kind))
    return cache[kind]


def live_declarations(*, now: datetime | None = None) -> list[HaltDeclaration]:
    """生效期覆盖 ``now`` 且尚未解除的声明——**不看开关**。

    与 ``blocking_declarations`` 分开，是因为「这个源想拦」与「这个源被启用了」是两个
    独立的事实，而合成一个函数之后就没法单独问前者了：窗口同步任务需要按前者对账
    （「我上次开的窗口现在还在不在表里」），而它不该受开关影响。
    """
    at = now or timezone.now()
    return list(
        HaltDeclaration.objects.filter(
            Q(closed_at__isnull=True)
            & Q(opened_at__lte=at)
            & (Q(expires_at__isnull=True) | Q(expires_at__gt=at))
        ).order_by("opened_at", "id")
    )


def blocking_declarations(*, now: datetime | None = None) -> list[HaltDeclaration]:
    """**此刻真的在拦**的声明：生效期内 ∧ 未解除 ∧ 开关打开。

    这是「现在在拦什么」的唯一答案。``query_halt`` 与 ``pre_trade_check`` 都从它出发，
    所以工具不会说出一个订单通路上不存在的层。
    """
    cache: dict[MechanismKind, bool] = {}
    return [
        row for row in live_declarations(now=now) if switch_open(trigger_of(row), cache)
    ]


def _matches(scope: str, symbol: str, strategy_id: str | None) -> bool:
    """这一行的作用域是否命中这次求值。

    **策略档的判据是 Q3 定死的**：调用方没给 ``strategy_id`` 时，表里的策略档行**不认它
    生效**。不认而不是「认成全市场」，是因为认了就等于「一条针对某个策略的停用把别人的
    单也拦了」；而如果第③段忘了把 id 带下来，这个选择的表现是**停用不生效**——它会在
    池化/停用日报里被看见（策略仍在下单），比「全场莫名停摆」容易得多。
    id 的来源有两处，都是**同一列**（`LiveSession.strategy_id`）：启动/恢复回显由视图
    直接传，下单通路由 `RiskGuard._strategy_of_session` 从 `live_session_id` 换出来。
    """
    kind, value = parse_scope(scope)
    if kind == "symbol":
        return value == symbol
    if kind == "strategy":
        return strategy_id is not None and value == str(strategy_id)
    return True  # global，以及读不懂的写法（`parse_scope` 的 fail-closed）


def layer_of(row: HaltDeclaration) -> HaltLayer:
    """一行声明 → 一层。**行 → 层的唯一换算口**，`halt_layers` 与 ``query_halt`` 都从这里
    走：调用方各自去 `row.trigger` / `row.scope` 里拼同一句话的话，拼法一旦分叉，
    「同一件事在两处说法不同」就回来了（与 ``scope_display`` 是同一条纪律）。
    """
    return HaltLayer(
        trigger=trigger_of(row),
        scope=row.scope,
        label=row.label,
        reason=row.reason,
        opened_at=row.opened_at,
        expires_at=row.expires_at,
    )


def halt_layers(
    symbol: str, strategy_id: str | None = None, *, now: datetime | None = None
) -> HaltVerdict:
    """**下单拦截的判定入口**：这一张单此刻挡不挡得住，被谁挡。

    ``symbol`` 是 ``OrderRequest.symbol``。``strategy_id`` 第②段没有调用方会给（下单
    通路上拿不到它），那时恒为 ``None``；第③段接上之后由 `RiskGuard._strategy_of_session`
    从 ``live_session_id`` 换出来带到这里，``_matches`` 里那条策略档判据才第一次真正生效。

    ``now`` 只用于求生效期，**不参与任何别的推断**：判定函数不看「今天是什么阶段」，
    也不看事件表。那些都在写入状态行的时候算完了。
    """
    at = now or timezone.now()
    layers = tuple(
        layer_of(row)
        for row in blocking_declarations(now=at)
        if _matches(row.scope, symbol, strategy_id)
    )
    return HaltVerdict(layers=layers)


def layers_touching(
    pairs: Iterable[tuple[str, str | None]], *, now: datetime | None = None
) -> tuple[HaltLayer, ...]:
    """一组「（品种, 策略）」上此刻在拦的层——``halt_layers`` 的**一对多**版本。

    ``halt_layers`` 回答的是「这一张单挡不挡得住」，而有些问题问的是「**这批人**还会被
    什么拦住」（第③段 Q3：一条窗口消息的受众）。两者的判据必须是同一个 ``_matches``：
    各自实现一遍的话，通知里说的「还剩几层」与订单通路上真实的拦法会分叉，而两处看起来
    都正常——正是「同一件事两个说法」的标准形态。

    ``pairs`` 为空（这批人一条活跃会话都没有）⇒ 返回空：没有单子，就没有会拦住它们的层。
    只查一次 ``blocking_declarations``：层的条数与「每条层命中哪些人」是两件事，前者不该
    随人数重复算。
    """
    wanted = tuple(pairs)
    if not wanted:
        return ()
    at = now or timezone.now()
    return tuple(
        layer_of(row)
        for row in blocking_declarations(now=at)
        if any(
            _matches(row.scope, symbol, strategy_id) for symbol, strategy_id in wanted
        )
    )


def layer_counts_touching(
    pairs: Iterable[tuple[str, str | None]], *, now: datetime | None = None
) -> dict[tuple[str, str | None], int]:
    """每一对「（品种, 策略）」此刻被**几层**拦——``halt_layers`` 的计数版。

    三个函数回答三个不同的问题：``halt_layers`` 问「这一张单挡不挡得住」，
    ``layers_touching`` 问「这批人还会被**什么**拦住」（层去重后的集合），本函数问
    「这批人**各自**还剩几层」。会话列表要的是第三个——每张卡片上一个「停 · N 层」，
    而 N 必须是真的：写死一个数、或者干脆不写数，卡片就成了一个不说数的警告。

    判据必须是同一个 ``_matches``，理由与 ``layers_touching`` 逐字相同：各自实现一遍的话，
    列表上说的「还剩几层」与订单通路上真实的拦法会分叉，而两处看起来都很正常。

    只查一次 ``blocking_declarations``：层的条数与「每条层命中哪些人」是两件事，前者不该
    随人数重复算。返回的字典**对每一对都有键**（没被拦是 ``0``，不是缺键）——缺键会让
    调用方把「没被拦」与「我没问你」混起来，而那两件事在前端长得一模一样。
    """
    wanted = tuple(pairs)
    if not wanted:
        return {}
    at = now or timezone.now()
    declarations = blocking_declarations(now=at)
    return {
        pair: sum(1 for row in declarations if _matches(row.scope, pair[0], pair[1]))
        for pair in wanted
    }


def block_reason(
    symbol: str, strategy_id: str | None = None, *, now: datetime | None = None
) -> str:
    """``halt_layers`` 的便利包装：没在拦时返回空串（= 放行）。"""
    return halt_layers(symbol, strategy_id, now=now).reason


# --------------------------------------------------------------------------- #
# 读面：交易页横幅看到的那些层（**惰性策略档不占位**）
# --------------------------------------------------------------------------- #

#: 哪些触发源的策略档声明**会**惰性——机制按「全部被管策略」产出，所以从没部署过的策略
#: 也占一条。只认机制自动产出的两档：`MANUAL` **不在此列**，理由见 `banner_layers`。
#: `EVENT` / `BLANKET` 也在此列之外：它们写的是 `global` 或 `symbol:` 作用域，与有没有
#: 会话无关（`_is_inert_strategy_layer` 的作用域判据是第二条闸，这里只列触发源）。
_INERT_ELIGIBLE_TRIGGERS = (HaltTrigger.DEACTIVATION, HaltTrigger.REGIME_SUSPENSION)


@dataclass(frozen=True)
class BannerVerdict:
    """交易页横幅要的答案：**看得到的层** + **没显示的惰性策略档条数**。

    两个字段一起才是完整的答案。只给 `layers` 的话，「库里还有 N 条策略档声明」
    这件事在界面上就查无此事——而它恰好是将来有人问「我明明停过它、界面怎么不说」
    时唯一能指向的东西。
    """

    layers: tuple[HaltLayer, ...]
    #: 被略去的惰性策略档条数（>0 时前端追加一行灰字）。**不是**「层数」——它不进
    #: `layers` 的规模，也不改变「此刻放不放行」这个结论。
    omitted_strategy_layers: int


def _is_inert_strategy_layer(
    row: HaltDeclaration, live_strategy_ids: frozenset[str]
) -> bool:
    """这条声明是不是「一条谁也拦不到的策略档声明」。

    三个条件缺一不可：

    1. **触发源是机制自动产出的**（`_INERT_PRONE_TRIGGERS`）。手动熔断不在此列：它是人
       当场确认过的停止，而横幅是它唯一的可见出口——滤掉它等于让一次人工停止在界面上
       静默消失。
    2. **作用域是 `strategy:`**。`global` 与 `symbol:` 与「有没有会话」无关：它们拦的是
       全场的答案 / 账户与品种上真实存在的单。
    3. **该策略此刻没有在跑**（`active_session_strategy_ids`）。

    判据 3 的「在跑」口径是 `deactivation_run.active_session_strategy_ids`（paper 与
    live 都算、状态取 `LiveSession.ACTIVE_STATUSES`）——**不在这里重写一遍**：这里少算
    一种模式，表现就是模拟盘会话的策略在横幅上被判成「没在跑」，方向恰好错向危险的那
    一侧（该看见的停止被藏起来了）。
    """
    if trigger_of(row) not in _INERT_ELIGIBLE_TRIGGERS:
        return False
    kind, value = parse_scope(row.scope)
    if kind != "strategy" or value is None:
        return False
    return value not in live_strategy_ids


def banner_layers(*, now: datetime | None = None) -> BannerVerdict:
    """交易页横幅的读口：**此刻在拦的层，略去惰性的策略档声明**。

    ## 为什么要有它，以及它为什么不是「第二个判定」

    池化与暂停两路都按**全部被管策略**产出声明行（`deactivation_run.managed_set` 的判据
    是注册表能不能解析到实现类，与有没有会话无关）。于是从没部署过的策略也在声明表里
    占一条，横幅上写着「策略 X 被停用」——而那一层**对任何一张单都是惰性的**：
    `_matches` 的策略档判据要求调用方给的 `strategy_id` 等于这一行，而 `strategy_id`
    只能由 `live_session_id` 换出来（`RiskGuard._strategy_of_session`）。没有会话 ⇒
    没有单会带这个 id ⇒ 它拦不到任何东西。

    所以**这**不是「放宽判定」，是**去掉一个恒为假的提示**：订单通路仍走 `halt_layers`，
    一个字都没变；本函数只服务读面。

    ## 两个字段，两类答案

    `layers` 是横幅该显示的那些层；`omitted_strategy_layers` 是被略去的条数。后者只
    报数、不报身份——本仓对「静默过滤」有成见（`HaltVerdict.reason` 的「只报一层会让
    人排查错方向」是同一类事故），所以那条信息必须留下一个入口，只是不该占一条层。

    `now` 只用于求生效期，与 `halt_layers` 同一条纪律：判定不看「今天是什么阶段」。
    """
    from apps.regime import deactivation_run

    at = now or timezone.now()
    rows = blocking_declarations(now=at)
    # 只有存在策略档行时才去问会话表：多数时刻声明表里只有保命档/事件档，而「此刻有谁
    # 在跑」是一次跨到 `trading` 的查询——为一次恒为空的过滤去查它没有意义。
    needs_sessions = any(parse_scope(row.scope)[0] == "strategy" for row in rows)
    live_ids: frozenset[str] = frozenset()
    if needs_sessions:
        live_ids = frozenset(
            str(strategy_id)
            for strategy_id in deactivation_run.active_session_strategy_ids()
            if strategy_id is not None
        )

    shown: list[HaltLayer] = []
    omitted = 0
    for row in rows:
        if _is_inert_strategy_layer(row, live_ids):
            omitted += 1
            continue
        shown.append(layer_of(row))
    return BannerVerdict(layers=tuple(shown), omitted_strategy_layers=omitted)
