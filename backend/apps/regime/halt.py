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
# **`BLANKET` 映射到 `None` 是刻意的、也是本表唯一一处不对称**：行情阶段 gate 那个开关
# （`REGIME_GATE`）要到第③段才接线，若把保命档挂在它下面，保命档就会被一个「默认关」
# 的开关关掉——而保命档是「高波动算生效」的，它不需要任何人确认。
#
# `DEACTIVATION` 指向第③段的开关，现在先写下来：让「策略档的声明归哪个源」有唯一答案
# （Q3 的判据要用到触发源），也免得第③段为了补一行映射又动一次迁移。
HALT_TRIGGER_SWITCH: dict[HaltTrigger, MechanismKind | None] = {
    HaltTrigger.BLANKET: None,
    HaltTrigger.EVENT: MechanismKind.EVENT_BREAKER,
    HaltTrigger.DEACTIVATION: MechanismKind.REGIME_GATE,
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


def _switch_open(trigger: HaltTrigger, cache: dict[MechanismKind, bool]) -> bool:
    """这个触发源的开关打开了没有。没有开关的触发源恒为真（保命档）。"""
    kind = HALT_TRIGGER_SWITCH.get(trigger)
    if kind is None:
        return True
    if kind not in cache:
        cache[kind] = RegimeMechanismSwitch.current(kind) is MechanismMode.EXECUTING
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
        row
        for row in live_declarations(now=now)
        if _switch_open(trigger_of(row), cache)
    ]


def _matches(scope: str, symbol: str, strategy_id: str | None) -> bool:
    """这一行的作用域是否命中这次求值。

    **策略档的判据是 Q3 定死的**：调用方没给 ``strategy_id`` 时，表里的策略档行**不认它
    生效**。不认而不是「认成全市场」，是因为认了就等于「一条针对某个策略的停用把别人的
    单也拦了」；而如果第③段忘了把 id 带下来，这个选择的表现是**停用不生效**——它会在
    池化/停用日报里被看见（策略仍在下单），比「全场莫名停摆」容易得多。
    第③段接进来时，必须同时把 id 从 ``live_session_id`` 那条路带到这里。
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

    ``symbol`` 是 ``OrderRequest.symbol``。``strategy_id`` 第②段没有调用方会给
    （下单通路上拿不到它），先留在签名里——第③段从 ``live_session_id`` 带下来之后，
    ``_matches`` 里那条策略档判据才会第一次真正生效。

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


def block_reason(
    symbol: str, strategy_id: str | None = None, *, now: datetime | None = None
) -> str:
    """``halt_layers`` 的便利包装：没在拦时返回空串（= 放行）。"""
    return halt_layers(symbol, strategy_id, now=now).reason
