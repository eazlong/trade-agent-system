"""停用决策的取数与落库（第①段单元 7iii 的收尾）。

`deactivation.py` 是纯函数：给一份格子与一个阶段，算出该停谁。本模块是它的另一半——
四条输入各自从哪里来、算出来的结论怎么写进 `DeactivationDecision`。

## 四条输入

| 输入 | 出处 |
|------|------|
| 当前阶段 | `judgement.current_judgement()`（「生效中」的语义只有它一处定义） |
| 当前代格子 | `pool_rebuild.current_cells(generation)`（「当前」= 最近一代 **ready**，也是它定义的） |
| 在期豁免 | `DeactivationExemption`（「在期」要读库、要时钟，纯函数不该碰） |

「在期豁免」这一行只有**读**方。写方是管理命令 `manage_deactivation_exemptions`——人发出的
恢复豁免只能由人写（Q7：「必须同时有写方」），推理路径永远不自己发豁免给自己放行。两侧共用
`CLOSE_REASON_*` 与 `in_force_exemptions` 的「三条一起」口径，所以不存在两份定义。
| 被管策略集合 | `Strategy` × `StrategyRegistry` × `LiveSession`，见下 |

## 被管策略集合：两条判据在这里的取数

判据一（能在注册表解析到实现类）**复用池化那条路**（`pool_rebuild.strategy_raw_texts`），
而不是另写一遍 `list_registered()` 成员判断：「解析得到实现类」在池化里已经有一个定义
（原型归属也依赖它），第二处定义迟早会漂移。代价是这一层与池化绑在一起——但被管策略
集合本来就是池化表的读法：判据一筛掉的那些正是回测自动建出来的幽灵策略行，它们在池化
里落在「未归类」。

判据二（被至少一个**活跃实盘**会话引用）**只标记，不筛选**——理由见 `deactivation`
的模块 docstring。所以这里读的是一个 `set`，交给推导去标。

`mode` 只认 `live`：判据二存在的意义是让第③段的 gate 知道该去停谁，而模拟盘不持仓真钱、
不会被停。`status` 用 `LiveSession.ACTIVE_STATUSES` 这个常量（模型上的那一份），不在这里
手抄状态字面量。

## 落库：`last_confirmed_at` 往前走，`evidence` 不动

`DeactivationDecision` 的 docstring 说得清楚：同一个结论第二次推导出来**不新建行**，只把
`last_confirmed_at` 往前推，而 `evidence` 冻在第一次判定的那一刻。所以这里逐行
`update_or_create`：

- `create_defaults`（新建用）= `{evidence: 冻住的那份, exemption: 指针, pool_rebuild_id, first_decided_at, last_confirmed_at}`
- `defaults`（已存在用）= `{last_confirmed_at}`

`exemption` 与 `evidence`/`pool_rebuild_id` **只出现在 `create_defaults` 里**是刻意的：它们记的是
「当初它是不是被豁免过」「当初依据的是哪一代」。豁免到期关掉之后再重跑一次，那一行仍要答
得出这两个问题——落进 `defaults` 会每轮把它们抹成 `None`，正是它们要避免的。
留下 `pool_rebuild_id` 也让它与 `evidence["pool_rebuild_id"]` 永远同值（重算差异重放
靠后者判断「这条决策的依据是不是已经过期」）。

决策要冻的是**它自己那一刻**的依据，而池化表每一代整体替换、日后还会重算——所以依据从
本轮那一次读出来的格子冻（`cells`），不从 `Outcome.evidence` 再抄一遍：同一份数据两处取用，
迟早会有一处忘了跟着改。冻下去的是**副本**，不是指向池化表的指针。

不用 `bulk_create`：要的是「推进」而不是「跳过已存在的」（`ignore_conflicts=True` 只做后者）。
一行一次 `update_or_create` 各自原子，也就**不必**再包一层事务：多条决策之间没有跨行不变量，
中途断了下一轮把剩下的补上——「补上」这件事每轮都在做，幂等本来就是它的形状。

## 冷启动、状态过期、没有池化表：三种「什么都不动」的收场

`deactivation.Derivation.blocked` 非空时 `writable` 必空，这里一行都不写，并把
`blocked_note` 带进返回值——「日志不算被看见」，这句话要由任务与日报接着往上传。

本层另有一种收场：**阶段有了、却一代池化表都没有**（`SKIPPED_NO_GENERATION`）。那不是
「没有建议」，是**没法问**——适用性表还没建过。它与前两种一样落成 `skipped`，一样带一句话。

三种收场下**豁免的关闭也一并停手**（`close_left_regime_exemptions` 只在没被挡下时才调）。
关豁免问的是「当前阶段是不是已经离开那条豁免登记的那个阶段」，而前两种收场说的正是「这一轮
说不清当前阶段」——冷启动没有阶段可问，过期的那条已经不作数。豁免本身还有「到期」这条
失效条件兜底（10 个自然日），所以停手的代价是有界的；反过来误关一条在期豁免**不可逆**
（`closed_at` 写下去就关掉了，没有复活路径）。两个方向不对称，就往不会造成不可逆损失的那边倒。

同一个理由还给了 `close_left_regime_exemptions` 自己第三条停手：**保命档期间一条都不关**
（详见它的 docstring）。高波动是叠加层而不是另一个阶段，豁免登记的基础阶段并没有离开——
但这一层「上面没被挡下」是看不出来的，所以那道闸放在那个函数自己身上。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
from uuid import UUID

from django.utils import timezone

from apps.regime import config, deactivation, judgement, pool_rebuild
from apps.regime.models import DeactivationDecision, DeactivationExemption
from apps.trading.models import LiveSession, Strategy

logger = logging.getLogger(__name__)

#: 阶段有、池化表没有。**不是「没有建议」，是「没法问」**（见模块 docstring）。
SKIPPED_NO_GENERATION = "no_generation"

#: 豁免提前失效的原因码（`DeactivationExemption.closed_reason`，max_length=32）。
#: 「阶段离开」是**系统**观测到的；「人工收回」是**人**下的手。两者分开记，日报与
#: 事后复盘才分得清「这条豁免是自然失效的，还是被人撤掉的」。
CLOSE_REASON_REGIME_LEFT = "regime_left"
CLOSE_REASON_MANUAL = "manual_revoke"

#: 原因码 → 中文。与 `deactivation.py` 的 `*_DISPLAY` 同一条纪律：展示与取值分开，
#: 中文只出现在展示层。两个码都在这里点名，所以新增一个码时这张表会缺一项——
#: 缺项在展示时回落成原因码本身，而不是伪装成另一档。
CLOSE_REASON_DISPLAY = {
    CLOSE_REASON_REGIME_LEFT: "阶段离开（自然失效）",
    CLOSE_REASON_MANUAL: "人工收回",
}


@dataclass(frozen=True)
class ManagedSet:
    """被管策略集合的取数结果（CONTEXT.md 第 105 条）。

    `ids` 是**筛过**的（判据一）；`running` 只是标记（判据二）；`unresolved` 是被判据一
    筛掉的那些——回测自动建出来的幽灵策略行，日报只给它们一个数，不给动作。
    """

    ids: tuple[UUID, ...]
    running: frozenset[UUID]
    unresolved: tuple[UUID, ...]


# --------------------------------------------------------------------------- #
# 取数
# --------------------------------------------------------------------------- #


def running_strategy_ids(strategy_ids: Iterable[UUID]) -> tuple[UUID, ...]:
    """被至少一个活跃实盘会话引用的策略 id（判据二，**只标记不筛选**）。"""
    ids = list(strategy_ids)
    if not ids:
        return ()
    rows = (
        LiveSession.objects.filter(
            strategy_id__in=ids,
            mode="live",
            status__in=LiveSession.ACTIVE_STATUSES,
        )
        .order_by("strategy_id")
        .values_list("strategy_id", flat=True)
        .distinct()
    )
    return tuple(rows)


def managed_set() -> ManagedSet:
    """被管策略集合。

    先补一次注册表发现（`ensure_strategies_discovered`）：注册表是「解析到实现类」的唯一
    出处，而 `apps.ready()` 只在策略目录当时存在时才 discover。不补的话整个集合会安静地
    缩成空——空集合在日报上看起来像「没有策略」，不像「注册表没加载」。

    排序按 `created_at`：集合是要进日报与任务返回值的，顺序不稳会让「今天和昨天有什么
    不同」多出一堆假的差异。
    """
    pool_rebuild.ensure_strategies_discovered()
    all_ids = list(
        Strategy.objects.order_by("created_at", "id").values_list("id", flat=True)
    )
    _, unresolved = pool_rebuild.strategy_raw_texts(all_ids)
    unresolved_set = set(unresolved)
    managed = tuple(i for i in all_ids if i not in unresolved_set)
    return ManagedSet(
        ids=managed,
        running=frozenset(running_strategy_ids(managed)),
        unresolved=tuple(unresolved),
    )


def current_regime_state(
    symbol: str = judgement.SYMBOL, *, now: datetime | None = None
) -> deactivation.RegimeState:
    """当前**生效**的那条阶段。没有生效判定（冷启动）时是 `RegimeState()`（`cold`）。

    判定的 `effective_at` 被原样带过去——过期判据问的是「这个结论从哪天开始在生效」，
    不是「它哪一天算出来的」。
    """
    record = judgement.current_judgement(symbol, now=now)
    if record is None:
        return deactivation.RegimeState()
    return deactivation.RegimeState(
        regime=record.effective_regime, effective_at=record.effective_at
    )


def in_force_exemptions(*, now: datetime | None = None) -> dict[tuple[UUID, str], int]:
    """在期人工豁免：`{(策略 id, 阶段): 豁免 id}`。

    三条一起才算在期——已关闭、还没生效、已过期都不算。「阶段离开」那一条由
    `close_left_regime_exemptions` 写成 `closed_at`，所以这里不重复判它。
    """
    now = now or timezone.now()
    rows = (
        DeactivationExemption.objects.filter(
            closed_at__isnull=True,
            granted_at__lte=now,
            expires_at__gt=now,
        )
        .order_by("granted_at")
        .values_list("id", "strategy_id", "regime")
    )
    # 同一格若重复发过豁免，取**最近发出的**那条：按 `granted_at` 升序遍历，字典后写
    # 覆盖先写。取错的话日报会说「已豁免」而实际在期的是另一条（更晚发的那条）。
    return {(strategy_id, regime): eid for eid, strategy_id, regime in rows}


def close_left_regime_exemptions(
    regime: str | None, *, now: datetime | None = None
) -> tuple[int, ...]:
    """阶段离开该阶段 → 豁免自然失效（CONTEXT.md 第 120 条）。返回被关掉的豁免 id。

    写成一条记录而不是纯查询派生，理由见 `DeactivationExemption` 的 docstring：阶段当天
    离开又回来时，纯按 `now < expires_at` 判断会让它复活，而那句话是「自然失效」不是
    「暂停」。

    冷启动（`regime is None`）时**一条都不关**：说不清「离开了没有」，而误关的代价是
    一条本来在期的豁免无声消失。

    **保命档期间同样一条都不关**，原因一模一样：高波动是**叠加层**（CONTEXT.md 第 115
    条），不是「离开」。豁免登记的永远是**基础阶段**，而高波动抬升期间传进来的这个
    `regime` 是 `high_vol`——照 `!=` 比下去，用户手上每一条在期豁免都会在高波动第一天
    被关掉，且没有复活路径；等保命档过去、系统回落到适用性层，那条策略直接进停用，
    而用户什么都没做。代价只是「阶段离开」的判定在高波动期间延后一轮，且豁免本身还有
    「到期」兜底（10 个自然日）——两个方向不对称，就往不会造成不可逆损失的那边倒。
    """
    if regime is None:
        return ()
    if deactivation.is_blanket(regime):
        return ()
    now = now or timezone.now()
    stale = list(
        DeactivationExemption.objects.filter(
            closed_at__isnull=True, expires_at__gt=now
        )
        .exclude(regime=regime)
        .values_list("id", flat=True)
    )
    if not stale:
        return ()
    # 更新时再查一次 `closed_at__isnull=True`：两次读之间可能有别人（管理命令）关掉了
    # 同一条，那时以先到者为准——后手不覆盖前手的 `closed_reason`。
    DeactivationExemption.objects.filter(id__in=stale, closed_at__isnull=True).update(
        closed_at=now, closed_reason=CLOSE_REASON_REGIME_LEFT
    )
    return tuple(stale)


def _frozen_evidence(
    outcome: deactivation.Outcome,
    row: Mapping[str, Any] | None,
    generation,
    state: deactivation.RegimeState,
) -> dict[str, Any]:
    """落进 `DeactivationDecision.evidence` 的那一份。

    「为什么停我」要能只读这一行就答完：当时是哪个阶段（以及它是从哪一刻生效的）、池化
    那一格写了什么（连同它自己的依据摘要）、依据的是哪一代池化表。

    `row` 为 `None` 在正常路径上到不了——能写出一条决策的前提就是那格 `unfit`，而有格才
    谈得上状态。留着这个分支只是为了让「那条链子哪天断了」退化成一条依据偏薄的决策，
    而不是在循环中间抛出去、把当天剩下的决策一起带走。
    """
    return {
        "regime": outcome.regime,
        "state": outcome.state,
        "reason": outcome.reason,
        "source": outcome.source,
        "needs_review": bool(row.get("needs_review")) if row else False,
        "pool_rebuild_id": generation.id,
        "pool_version": generation.pool_version,
        "regime_effective_at": (
            state.effective_at.isoformat() if state.effective_at else None
        ),
        # 池化那一格的依据摘要原文（`RegimePoolCell.evidence`）。池化表会被下一代整体
        # 替换，所以它必须抄在这里，不能留个指针。
        "cell": dict(row.get("evidence") or {}) if row else {},
    }


# --------------------------------------------------------------------------- #
# 落库
# --------------------------------------------------------------------------- #


def run_deactivation(
    *,
    symbol: str = judgement.SYMBOL,
    now: datetime | None = None,
    params: config.JudgementLifecycleConfig | None = None,
) -> dict[str, Any]:
    """当前阶段 × 当前代池化格 → 停用决策（`DeactivationDecision`，状态恒为 `suggested`）。

    幂等：同一个结论反复跑只会把 `last_confirmed_at` 往前推。返回一个**可 JSON 序列化**的
    摘要（Celery 用 JSON 序列化结果，所以 id 一律是字符串），键集在两条路径上一致。

    `skipped` 恒存在，取值 `None` / `cold_start` / `stale_state` / `no_generation`。
    与判定的「有问题才给 `skipped`」略有不同：这里日报要能直接问一句「这轮有没有产出」，
    而不是靠键在不在——**一个会因为缺键而变成「没事」的字段，迟早会被当成没事**。
    非空时 `note` 必然是一句给人看的话（`deactivation.Derivation.blocked_note`），
    由调用方带出去：日志不算被看见。

    Args:
        symbol: 判定与池化都挂在同一个品种上（`config.CANDLES.symbol`）。
        now: 注入时钟；不传取当前时刻。同时用于在期豁免与过期判据。
        params: 生命周期参数；不传取统一配置面的当前值。
    """
    now = now or timezone.now()
    params = params or config.JUDGEMENT_LIFECYCLE

    managed = managed_set()
    state = current_regime_state(symbol, now=now)
    generation = pool_rebuild.current_generation()

    summary: dict[str, Any] = {
        "symbol": symbol,
        "regime": state.regime,
        "generation_id": generation.id if generation is not None else None,
        "skipped": None,
        "note": "",
        "managed": len(managed.ids),
        "running": len(managed.running),
        "unresolved": [str(i) for i in managed.unresolved],
        "unmanaged": [],
        "targets": 0,
        "written": 0,
        "created": 0,
        "confirmed": 0,
        "exempt": 0,
        "needs_review": 0,
        "missing_cells": 0,
        "exemptions_closed": 0,
        "suggestions": [],
    }

    if generation is None:
        summary["skipped"] = SKIPPED_NO_GENERATION
        summary["note"] = (
            "还没有任何一代池化表，本轮不产出停用建议（先跑 recompute_regime_slices）"
        )
        logger.info("[regime] 停用决策：%s", summary["note"])
        return summary

    # 一次读出当前代（**这一代**）的全部格子，推导与冻证据都从这一份取。
    # 不分成两次读：两次各自去问一次当前代，之间可能正好翻了一代，于是落进决策的
    # 就成了「依据写的是第 N 代、引用的却是第 N+1 代那一格」。
    cells = pool_rebuild.current_cells(generation)
    derivation = deactivation.derive(
        cells,
        state=state,
        strategy_ids=managed.ids,
        running_ids=managed.running,
        exemptions=in_force_exemptions(now=now),
        now=now,
        params=params,
    )

    summary.update(
        {
            "unmanaged": [str(i) for i in derivation.unmanaged],
            "targets": len(derivation.targets),
            "exempt": len(derivation.exempt),
            "needs_review": len(derivation.needs_review),
            "missing_cells": len(derivation.missing_cells),
        }
    )

    if derivation.blocked:
        # 冷启动 / 状态过期：一条都不写，但**必须留下这句话**（见模块 docstring）。
        # 豁免的关闭也一并停手——见模块 docstring 里「三种什么都不动」那一段。
        summary["skipped"] = derivation.blocked
        summary["note"] = derivation.blocked_note
        logger.info("[regime] 停用决策：%s", summary["note"])
        return summary

    summary["exemptions_closed"] = len(close_left_regime_exemptions(state.regime, now=now))
    for outcome in derivation.writable:
        row = cells.get((outcome.strategy_id, outcome.regime))
        if row is None:
            # 单次读取下这里到不了：`TARGET` 只能由一格 `unfit` 推出来，而有格才推得出。
            # 留着它只是让「那条链子哪天断了」退化成一行依据偏薄的决策，而不是在循环
            # 中间抛出去、把当天剩下的决策一起带走——`_frozen_evidence` 收 `None`。
            logger.warning(
                "[regime] 停用决策：当前代池化表里找不到策略 %s 在 %s 的格子，依据留空",
                outcome.strategy_id,
                outcome.regime,
            )
        _, created = DeactivationDecision.objects.update_or_create(
            strategy_id=outcome.strategy_id,
            regime=outcome.regime,
            # `defaults` 只管**更新**；`create_defaults` 只管**新建**（不传时新建也用
            # `defaults`）。两边分开正是要的形状：已存在的那一行只被推进 `last_confirmed_at`。
            defaults={"last_confirmed_at": now},
            create_defaults={
                "evidence": _frozen_evidence(outcome, row, generation, state),
                "exemption_id": outcome.exempt_id,
                "pool_rebuild_id": generation.id,
                "first_decided_at": now,
                "last_confirmed_at": now,
            },
        )
        summary["created" if created else "confirmed"] += 1
        summary["suggestions"].append(
            {
                "strategy_id": str(outcome.strategy_id),
                "regime": outcome.regime,
                "state": outcome.state,
                "reason": outcome.reason,
                "source": outcome.source,
                "running": outcome.running,
                "exempt": outcome.exempt_id is not None,
                "created": created,
            }
        )

    summary["written"] = len(summary["suggestions"])
    logger.info(
        "[regime] 停用决策：阶段 %s，被管 %s（在跑 %s），建议 %s（新建 %s / 确认 %s），"
        "已豁免 %s，关闭豁免 %s，待复核 %s，无此格 %s",
        summary["regime"],
        summary["managed"],
        summary["running"],
        summary["targets"],
        summary["created"],
        summary["confirmed"],
        summary["exempt"],
        summary["exemptions_closed"],
        summary["needs_review"],
        summary["missing_cells"],
    )
    return summary
