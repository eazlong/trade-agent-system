"""重算差异重放（第①段单元 7 的收尾）：新表 vs 生效中的停用决策。

CONTEXT.md 第 118 条把这件事的两头都钉死了：**重算不自动恢复任何停用决策**（「恢复需
人工确认」是这份设计的地基之一），但**必须把差异摆到人面前**——重算完成后对每一条
生效中的停用决策重放一遍新表：仍判为不适用／中性的原样保留，**变为适用的标记为
「依据已随重算失效」**，并计入当日日报第②段与一次即时告警。

停用决策记录本身**不被覆盖**：新表结论作为一条独立的差异记录（`DeactivationReview`）
挂在它旁边。覆盖会让「为什么当初停我」永远答不出来。

## 重放问的是「那一格」，不是「当前阶段」

一条停用决策问的是「这条策略在**它记的那个阶段**上该不该跑」（键是策略 × 阶段）。
所以重放读的格子是 `(策略, decision.regime)`——与当下生效的是哪个阶段无关。这不是
顺手写成的：拿「当前阶段」去重放，会让一条登记在下跌段的决策在行情转成震荡之后被问
一句它从没答过的问题（震荡段适不适用），而那句答案与这条决策的成立与否无关。

同理，重放**不经过** `deactivation.derive`：那条路先读当前阶段、再取那一列的格子，
而它还有一个「冷启动 ⇒ 一条结论都不给」的短路——重放没有「当前阶段」这个概念，
不能被那个短路吞掉。

## 三值词表：先收敛，再判

`ReviewVerdict` 只有三档，池化的状态有五档，所以这里先收敛再判：

- `unfit` → `STILL_UNFIT`。新表仍判这条策略在这个阶段不适用，这条决策照样成立。
  `needs_review`（方向冲突）**不改变结论**：冲突说的是「依据要人看一眼」，不是「变成
  适用了」。它是如实记进证据、也如实带进日报的一个标记，不是第四档结论。
- `fit` → `BECAME_FIT`。**唯一**会触发告知的那一档。
- 其余（`neutral` / `unknown` / `blanket` / 那一格没了）→ `STILL_NEUTRAL`。

收敛用的是 `deactivation.VERDICT_OF_STATE`（池化状态 → 逐策略结论的唯一出处），不在这里
另抄一份状态字面量表。于是**认不出来的状态取值走的是 `UNKNOWN`**，落成 `STILL_NEUTRAL`，
绝不可能被读成「依据已失效」——与 `deactivation._verdict` 是同一个 fail-safe 方向：
认不出来就什么都不说，既不说「该停」，也不说「可以跑」。

注意那张表**只覆盖四个状态**：`unfit` 不在表里，因为它在 `deactivation` 那里走的是另一条
路（`TARGET`／`NEEDS_REVIEW`）。所以这里的 `unfit` 分支必须排在查表之前——漏了它，`unfit`
会落到 `UNKNOWN`，于是一条明明仍成立的停用决策会被重放成「中性」。两侧合起来才盖满
`slice` 的四个 `STATE_*`，测试（`set(VERDICT_OF_STATE) | {STATE_UNFIT}` 必须等于那一集合）
在编译期之外把这件事钉住。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from apps.regime import deactivation
from apps.regime.models import ReviewVerdict
from apps.regime.quant import BaseRegime
from apps.regime.slice import STATE_UNFIT


@dataclass(frozen=True)
class DecisionRef:
    """一条要被重放的停用决策。**只带重放要问的那几列**（取数在 DB 侧那一半）。

    不带 `status`／`evidence`：重放判的是「新表怎么说」，决策自己的状态与冻结依据
    进了这条重放记录之后仍然是它自己的事（`DeactivationReview` 挂的是外键）。
    """

    id: int
    strategy_id: UUID
    regime: str


@dataclass(frozen=True)
class Review:
    """一条决策在**这一代**新表上的重放结论。字段与 `DeactivationReview` 一一对应。"""

    decision_id: int
    strategy_id: UUID
    regime: str
    verdict: str
    #: 新表那一格的结论三元组 + 冲突标记。原样带出来，报告要照抄池化的话，
    #: 而不是在这里重写一句可能与之不一致的话。
    state: str = ""
    reason: str = ""
    source: str = ""
    needs_review: bool = False
    #: 新表那一格的依据摘要原文（`RegimePoolCell.evidence`）。池化表会再换代，所以
    #: 它必须**抄**进差异记录，不能留个指针。
    cell: Mapping[str, Any] = field(default_factory=dict)
    #: 上一次重放对同一条决策的结论（没有则 `None`）。「相对上一代的差异」靠它。
    previous_verdict: str | None = None

    @property
    def display(self) -> str:
        return ReviewVerdict(self.verdict).display

    @property
    def invalidated(self) -> bool:
        """「依据已随重算失效」——CONTEXT.md 第 118 条里唯一要动的那一档。"""
        return self.verdict == ReviewVerdict.BECAME_FIT.value

    @property
    def changed(self) -> bool:
        """与上一次重放的结论不同。**首次重放也算变了**：从没说过话到说了话。"""
        return self.previous_verdict != self.verdict


def review_verdict(row: Mapping[str, Any] | None) -> str:
    """新表的一格 → 这次重放的结论。取不到那一格（`None`）算 `STILL_NEUTRAL`。

    取不到与「新表说中性」在这里落成同一档，是刻意的：三值词表答不了「那一格没了」
    这个第四种情况，而落到哪一档决定了要不要惊动人。**唯一该惊动人的只有 `fit`**，
    所以其余一律落到「没事」那一档，并把真实取值（`state` 为空表示没有格）如实记进
    证据供人查。反过来落成 `BECAME_FIT` 会让一次全表重算之后的每一格都变成告警。
    """
    if row is None:
        return ReviewVerdict.STILL_NEUTRAL.value
    state = row.get("state") or ""
    if state == STATE_UNFIT:
        return ReviewVerdict.STILL_UNFIT.value
    # 不在表里的取值落到 `deactivation.UNKNOWN`，于是绝不会是 `FIT`（见模块 docstring）。
    verdict = deactivation.VERDICT_OF_STATE.get(state, deactivation.UNKNOWN)
    if verdict == deactivation.FIT:
        return ReviewVerdict.BECAME_FIT.value
    return ReviewVerdict.STILL_NEUTRAL.value


def plan_reviews(
    decisions: Iterable[DecisionRef],
    cells: Mapping[tuple[UUID, str], Mapping[str, Any]],
    *,
    previous: Mapping[int, str] | None = None,
) -> tuple[Review, ...]:
    """逐条决策算出它的重放结论。**纯函数**：不读库、不写库、不看时钟。

    顺序按 `(策略 id, 阶段)` 定死：这批结论要进日报与告警，顺序不稳会让「今天和昨天
    有什么不同」多出一堆假差异。

    Args:
        decisions: 要重放的决策。
        cells: `pool_rebuild.current_cells()` 的形状：`{(策略 id, 阶段): 行字典}`。
        previous: `{决策 id: 上一次重放的结论}`。不传即视为「从没重放过」。
    """
    previous = previous or {}
    planned: list[Review] = []
    for decision in sorted(decisions, key=lambda d: (str(d.strategy_id), d.regime)):
        row = cells.get((decision.strategy_id, decision.regime))
        planned.append(
            Review(
                decision_id=decision.id,
                strategy_id=decision.strategy_id,
                regime=decision.regime,
                verdict=review_verdict(row),
                state=(row.get("state") or "") if row else "",
                reason=(row.get("reason") or "") if row else "",
                source=(row.get("source") or "") if row else "",
                needs_review=bool(row.get("needs_review")) if row else False,
                cell=dict(row.get("evidence") or {}) if row else {},
                previous_verdict=previous.get(decision.id),
            )
        )
    return tuple(planned)


def count_verdicts(reviews: Iterable[Review]) -> dict[str, int]:
    """三档各自的条数。**三个键恒存在**（0 也留着）。

    与 `pool_rebuild.EXCLUSION_KEYS` 同一条纪律：一个恒定的键集让「这一档从没出现过」
    与「这一档被改名了」能分开。取值一定在表里（`review_verdict` 只会产出它们），
    所以这里是直接下标而不是 `get`——真出了表外的取值应当立刻炸，而不是被悄悄收编。
    """
    counts = {verdict.value: 0 for verdict in ReviewVerdict}
    for review in reviews:
        counts[review.verdict] += 1
    return counts


def format_alert(
    invalidated: Sequence[Mapping[str, Any]], *, rebuild_id: int | None = None
) -> str:
    """给人看的一条即时告警。**只描述，不含动作。**

    CONTEXT.md 明令不自动恢复，所以这句话不能读成「已恢复」，只能读成「依据没了，
    请你决定」。最后一行把出口指出来，否则收到告警的人唯一能做的就是来问。

    `invalidated` 的形状就是 `replay_run.run_replay` 返回值里那个键：每条含
    `strategy_id` / `name` / `regime` / `previous_verdict`。
    """
    head = f"⚠️ 池化表重算后，{len(invalidated)} 条停用建议的依据已失效"
    lines = [head]
    for item in invalidated:
        who = item.get("name") or item.get("strategy_id")
        regime = BaseRegime(item["regime"]).display
        lines.append(f"· {who} @ {regime}")
    lines.append("**不会自动恢复**：确认要放它跑，请用人工豁免命令。")
    return "\n".join(lines)
