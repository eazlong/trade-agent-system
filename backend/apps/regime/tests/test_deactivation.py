"""停用决策推导（第①段单元 7iii）。

这个文件要钉住的不是「哪条策略该停」，而是**七条性质**：

1. **每个被管策略恰好一条结论**——不多不少。少了会让某条策略静默地逃出机制，多了
   会让「这条策略被停了几次」变成一个算不出来的数。
2. **`TARGET` 是唯一会落决策的取值**。`neutral`/`fit`/`unknown`/`no_cell` 都不是
   停用理由：门槛没过是「没结论」，不是「不好」；该阶段没有交易日是「说不出来」，
   不是「不适用」。
3. **冲突格子阻塞建议、不阻塞结论**（Q3）：`unfit + needs_review` 落成 `NEEDS_REVIEW`，
   不在 `targets` 里，但那格的摘要照旧带着走。
4. **保命档与「判不出方向」是两条显式早退**（#66）：保命档 → `BLOCKED_BLANKET`，
   `state.cell is None`（`unclear` / 旧行无 cycle）→ `BLOCKED_NO_CELL`。换轴之后
   「高波动恒为一格」那个结构性保证不存在了，这两道闸必须显式写出来，否则会按
   一个方向给出停用建议——理由完全不同，而动作要人工恢复。
5. **冷启动与状态过期都不产出建议，但都必须留下可说的一句话**——不静默是这条纪律的
   一半，另一半是这句话得由调用方带出去。
6. **被管策略集合的两条判据是一筛一标**：解析不到实现类的（不在 `strategy_ids` 里）
   只报数不推导；有活跃会话的只是被标记。
7. **词表是落库契约**：`state`/`reason` 在模型上是裸 `CharField`，所以「池化的词表被
   覆盖全了、且认不出来的取值不会变成『该停』」必须由本文件钉住。

门槛一律用**缩小**的那组生命周期参数（与 `test_pool.py` 同一做法）：要造很久的时钟才
出结论，断言只会被埋进日期算术里。缩小的只是规模。注意 `min_dwell_days` 在本模块
**不参与任何计算**（`_gate` 只读 `stale_after_days`），传它只是为了给全同一份配置类。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from apps.regime import deactivation as dea
from apps.regime import slice as sl
from apps.regime.config import JudgementLifecycleConfig
from apps.regime.quant import BaseRegime, Cycle

#: 四档生效阶段的 slug。保命档判据、展示、日报快照仍在读它。
HIGH_VOL = BaseRegime.HIGH_VOL.value
DOWNTREND = BaseRegime.DOWNTREND.value
UPTREND = BaseRegime.UPTREND.value

#: 格子轴（#66）：池化表的键是 cycle 三格，与上面的四档阶段**不是**同一个东西。
BULL = Cycle.BULL.value
BEAR = Cycle.BEAR.value
SIDEWAYS = Cycle.SIDEWAYS.value

#: 生命周期参数：过期门槛 3 天（默认值）+ 一个不参与本模块的待机天数（同为默认值）。
LIFE = JudgementLifecycleConfig(min_dwell_days=3, stale_after_days=3)

DAY0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def at(days: int = 0, hours: int = 0) -> datetime:
    return DAY0 + timedelta(days=days, hours=hours)


def cell(
    strategy_id: int,
    regime: str,
    state: str,
    *,
    reason: str = "",
    source: str = "strategy",
    needs_review: bool = False,
    evidence: dict | None = None,
) -> dict:
    """一格池化结论的字面量，形状与 `pool_rebuild.current_cells()` 一致。"""
    return {
        "strategy_id": strategy_id,
        "regime": regime,
        "source": source,
        "state": state,
        "reason": reason,
        "evidence": evidence if evidence is not None else {"trades": 42},
        "needs_review": needs_review,
    }


def derive(cells, *, state=DOWNTREND, cell_key=BEAR, ids=(1,), now=None, **kwargs):
    """把 `derive` 的调用压到一行，好让每个用例只剩它真正在问的那一件事。

    `state` 是四档的**生效阶段**（保命档、过期判据读它），`cell_key` 是池化表的
    **格子键**（cycle 三格之一）。两者必须分开传：#66 之后拿四档去查新代必然落空，
    而那个空落恰是我们特意要覆盖的 `BLOCKED_NO_CELL` 分支。
    """
    return dea.derive(
        cells,
        state=dea.RegimeState(regime=state, cycle=cell_key, effective_at=at()),
        strategy_ids=ids,
        now=now or at(),
        params=LIFE,
        **kwargs,
    )


class TestOneOutcomePerManagedStrategy(SimpleTestCase):
    """性质 1：不重不漏。"""

    def test_every_managed_strategy_gets_exactly_one_outcome(self):
        cells = {
            (1, BEAR): cell(1, BEAR, sl.STATE_UNFIT),
            (2, BEAR): cell(2, BEAR, sl.STATE_FIT),
        }
        got = derive(cells, ids=(1, 2, 3))
        self.assertEqual([o.strategy_id for o in got.outcomes], [1, 2, 3])
        self.assertEqual(len(got.outcomes), 3)

    def test_a_managed_strategy_without_a_cell_is_no_cell_not_unknown(self):
        """没有格子与「格子结论是 unknown」是两件事：前者是没有结论，后者是结论就是
        「该阶段没有交易日」。混成一个会让日报说「该阶段没出现过」而其实是它的回测
        全被排除了。"""
        got = derive({}, ids=(7,))
        self.assertEqual(got.outcomes[0].verdict, dea.NO_CELL)
        self.assertEqual(got.outcomes[0].state, "")
        self.assertEqual(got.targets, ())

    def test_duplicate_ids_do_not_produce_duplicate_outcomes(self):
        got = derive({}, ids=(1, 1, 2))
        self.assertEqual([o.strategy_id for o in got.outcomes], [1, 2])

    def test_only_the_managed_set_is_derived(self):
        cells = {
            (1, BEAR): cell(1, BEAR, sl.STATE_UNFIT),
            (99, BEAR): cell(99, BEAR, sl.STATE_UNFIT),
        }
        got = derive(cells, ids=(1,))
        self.assertEqual([o.strategy_id for o in got.outcomes], [1])
        self.assertEqual(got.unmanaged, (99,))

    def test_a_cell_in_another_cycle_neither_leaks_in_nor_counts_as_managed(self):
        """只读当前那一格。别的格的行进来只是为了算 `unmanaged`，不是候选。

        「另一个格」在 #66 之后是另一个**周期**（这里当前是 `bear`、表里有 `bull`），
        不再是「另一个档位」——格子轴换成 cycle 之后，四档那套键根本进不了这张表。
        """
        cells = {
            (1, BULL): cell(1, BULL, sl.STATE_UNFIT),
            (99, BULL): cell(99, BULL, sl.STATE_FIT),
        }
        got = derive(cells, ids=(1,), cell_key=BEAR)
        self.assertEqual(got.outcomes[0].verdict, dea.NO_CELL)
        self.assertEqual(got.unmanaged, (99,))


class TestWhatIsNotAGroundForDeactivation(SimpleTestCase):
    """性质 2 + 7：只有 `unfit` 会变成建议，且池化的词表被覆盖全了。"""

    def test_every_pool_state_is_mapped_and_unfit_is_the_one_handled_apart(self):
        """池化**可产出**的四个状态一个不落地有归宿，`unfit` 是唯一在表外单独处理的那个。

        这条红了通常意味着有人给 `pool` 加了第五种状态而没告诉这一层——那时
        `_verdict` 会把新状态当 `unknown`（fail-safe），而不会变成「该停」。

        #66 之后预期集合就是 `slice.STATE_VALUES` 本身（`fit`/`unfit`/`neutral`/
        `unknown`）：`blanket` 已不在那个集合里，池化也不再产出它。
        """
        self.assertEqual(
            set(dea.VERDICT_OF_STATE) | {sl.STATE_UNFIT},
            set(sl.STATE_VALUES),
        )
        self.assertNotIn(sl.STATE_UNFIT, dea.VERDICT_OF_STATE)

    def test_state_to_verdict_is_one_to_one(self):
        """表里的三档逐一对应，`unfit` 走另一条路（`TARGET`／`NEEDS_REVIEW`）。"""
        expected = {
            sl.STATE_FIT: dea.FIT,
            sl.STATE_NEUTRAL: dea.NEUTRAL,
            sl.STATE_UNKNOWN: dea.UNKNOWN,
        }
        for state, verdict in expected.items():
            with self.subTest(state=state):
                got = derive({(1, BEAR): cell(1, BEAR, state)}, ids=(1,))
                self.assertEqual(got.outcomes[0].verdict, verdict)

    def test_only_target_reaches_the_suggestion_list(self):
        """逐状态过一遍 `targets`：这是「哪些结论构成停用理由」的全集断言。"""
        for state in (
            sl.STATE_FIT,
            sl.STATE_NEUTRAL,
            sl.STATE_UNKNOWN,
        ):
            with self.subTest(state=state):
                got = derive({(1, BEAR): cell(1, BEAR, state)}, ids=(1,))
                self.assertEqual(got.targets, ())
                self.assertEqual(got.writable, ())

    def test_an_unrecognized_state_never_becomes_a_suggestion(self):
        """词表漂移时**失败的方向**是「不停」，不是「停」。"""
        got = derive({(1, BEAR): cell(1, BEAR, "brand_new_state")}, ids=(1,))
        self.assertEqual(got.outcomes[0].verdict, dea.UNKNOWN)
        self.assertEqual(got.targets, ())
        # 原样留着，好让人看见那一格到底写了什么。
        self.assertEqual(got.outcomes[0].state, "brand_new_state")

    def test_every_verdict_has_a_display(self):
        """日报直接抄这句话，所以每个取值都得有一句人话。"""
        for verdict in (
            dea.TARGET,
            dea.NEEDS_REVIEW,
            dea.FIT,
            dea.NEUTRAL,
            dea.UNKNOWN,
            dea.BLANKET,
            dea.NO_CELL,
        ):
            with self.subTest(verdict=verdict):
                self.assertIn(verdict, dea.VERDICT_DISPLAY)
                self.assertTrue(dea.VERDICT_DISPLAY[verdict])


class TestConflictBlocksTheSuggestionNotTheConclusion(SimpleTestCase):
    """性质 3。"""

    def test_a_conflicted_unfit_cell_is_not_a_target(self):
        got = derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT, needs_review=True)},
            ids=(1,),
        )
        self.assertEqual(got.outcomes[0].verdict, dea.NEEDS_REVIEW)
        self.assertEqual(got.targets, ())
        self.assertEqual([o.strategy_id for o in got.needs_review], [1])

    def test_the_conclusion_still_comes_through(self):
        """结论照常产出、照常被引用：状态与原因原样带着走。"""
        got = derive(
            {
                (1, BEAR): cell(
                    1,
                    BEAR,
                    sl.STATE_UNFIT,
                    reason=sl.REASON_DIRECTION_CONFLICT,
                    needs_review=True,
                )
            },
            ids=(1,),
        )
        self.assertEqual(got.outcomes[0].state, sl.STATE_UNFIT)
        self.assertEqual(got.outcomes[0].reason, sl.REASON_DIRECTION_CONFLICT)

    def test_a_clean_unfit_cell_is_still_a_target(self):
        """对照组：同一条 `unfit`，只是不冲突。否则上一条可能是「unfit 都不产建议」。"""
        got = derive({(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)}, ids=(1,))
        self.assertEqual([o.strategy_id for o in got.targets], [1])


class TestBlanketIsAnExplicitGate(SimpleTestCase):
    """性质 4：保命档到不了——#66 起这条闸是**显式**的，不再靠池化的结构保证。"""

    def test_a_high_vol_regime_produces_no_target_even_from_an_unfit_shaped_cell(self):
        """保命档期间就算表里有一格「不适用」，也不产出任何结论。

        换轴之前这是结构性的：池化对高波动那一格恒返回 `blanket`，而 `blanket` 不是
        `TARGET`，所以没有代码路径能产建议。格子换成 cycle 之后保命档期间
        `state.cell` 仍是一个有效方向键，照走就会按那个方向给出 `unfit` 建议——
        于是这道闸只能由 `derive` 显式早退（`BLOCKED_BLANKET`）来顶。
        """
        got = derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)},
            ids=(1,),
            state=HIGH_VOL,
        )
        self.assertEqual(got.blocked, dea.BLOCKED_BLANKET)
        self.assertEqual(got.outcomes, ())
        self.assertEqual(got.targets, ())
        self.assertEqual(got.writable, ())
        self.assertTrue(got.blocked_note)

    def test_the_state_knows_it_is_the_blanket(self):
        """日报要写一句「全场停用由保命档负责」，那句的依据是**阶段本身**，
        与这一代池化表算出了什么无关。"""
        state = dea.RegimeState(regime=HIGH_VOL, effective_at=at())
        self.assertTrue(state.blanket)
        self.assertFalse(dea.RegimeState(regime=DOWNTREND, effective_at=at()).blanket)


class TestTheCellKeyIsTheCycle(SimpleTestCase):
    """`RegimeState.cell` 是「四档生效阶段 → cycle 格子键」的唯一一处换算（#66）。

    散成两处的话，总有一处会拿 `downtrend` 去新代找格子——那必然落 `no_cell`，
    于是一条本该正常推导的策略被 fail-closed 拦下，而拦它的理由还是错的。
    """

    def test_the_three_cycle_cells_pass_through(self):
        for cycle in (BULL, BEAR, SIDEWAYS):
            with self.subTest(cycle=cycle):
                self.assertEqual(dea.RegimeState(cycle=cycle).cell, cycle)

    def test_no_direction_has_no_cell(self):
        """`unclear` 与 `None` 都不是第三、第四个方向：没有格子键可问。"""
        self.assertIsNone(dea.RegimeState(cycle="unclear").cell)
        self.assertIsNone(dea.RegimeState().cell)

    def test_the_four_tier_regime_is_not_mistaken_for_a_cell(self):
        """生效阶段是四档，与格子键不同名。`downtrend` 不是 cycle，不能直接当键用。"""
        self.assertIsNone(dea.RegimeState(regime=DOWNTREND, cycle=None).cell)


class TestBlanketReadsTheVolatilityAxis(SimpleTestCase):
    """保命档的判据自 #65 起是**波动维**，不是某个档位名。

    ADR 0007 把高波动从「四档之一」改成「叠加层」——它不再是一个阶段，而是叠加在阶段
    上的一个性质。判据跟着换源之后，这里钉三件事：新行读波动维、旧行回落 slug、
    以及**两个判据取或**（少认一次保命档的代价是不可逆的解除）。
    """

    def test_the_volatility_axis_alone_can_declare_the_blanket(self):
        """`volatility=event_spike` 单独就足以判保命档，不必 slug 配合。

        正常数据下两者恒等（写入路径由波动维派生 `effective_regime`），所以这一条
        用一个人为不一致的组合来证明**波动维确实是判据之一**——若判据还只看 slug，
        它就会红。
        """
        self.assertTrue(
            dea.RegimeState(
                regime=DOWNTREND, volatility="event_spike", effective_at=at()
            ).blanket
        )

    def test_a_legacy_row_without_the_axis_still_counts_as_blanket(self):
        """旧行（回滚窗口内写的 / 迁移没回填出来的）`volatility` 为空 ⇒ 按 slug 判。

        「读不到新列」绝不能被读成「不是保命档」：那会让保命档第一天就解除全场，
        而解除没有复活路径。
        """
        self.assertTrue(dea.RegimeState(regime=HIGH_VOL, volatility=None).blanket)
        self.assertFalse(dea.RegimeState(regime=DOWNTREND, volatility=None).blanket)

    def test_the_two_criteria_are_ored_so_a_stale_axis_never_unblanks_the_system(self):
        """**两个判据取或**——这是本层最要紧的一格。

        库里有一整类行是「基础阶段横盘、资讯抬升成 `high_vol`」：它们的两维按
        `base_regime` 回填（`range` ⇒ `normal`），于是 `volatility` **有值**却说
        「没有事件波动」，而 `effective_regime` 正停着全场。只读波动维会把这类行
        读成「不是保命档」——保命档的第一天就把全场解除。
        """
        legacy_escalated = dea.RegimeState(
            regime=HIGH_VOL, base_regime="range", cycle="sideways", volatility="normal"
        )
        self.assertTrue(
            legacy_escalated.blanket,
            "slug 说是保命档而波动维说是 normal 时，取向必须是「多认一次保命档」",
        )

    def test_the_two_helpers_agree_with_the_property(self):
        """`is_blanket` / `is_event_spike` 是这套判据的唯一实现，属性只是把它接到
        读面上。三者分家的话，「谁算保命档」就会有两处口径。"""
        self.assertTrue(dea.is_blanket(HIGH_VOL))
        self.assertTrue(dea.is_event_spike("event_spike"))
        for value in (None, "", DOWNTREND, "range", "unclear", "normal"):
            with self.subTest(value=value):
                self.assertFalse(dea.is_blanket(value))
                self.assertFalse(dea.is_event_spike(value))


class TestBlocked(SimpleTestCase):
    """性质 5：不产出，但要说出来。"""

    def test_cold_start_produces_no_outcomes_at_all(self):
        """没有当前阶段可问：逐策略全落成「没有这一格」是一句真的废话，而且会把一次
        「没有阶段」摊成几十条噪声。收场只有一条 `blocked`。"""
        got = dea.derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)},
            state=dea.RegimeState(),
            strategy_ids=(1,),
            now=at(),
            params=LIFE,
        )
        self.assertEqual(got.blocked, dea.BLOCKED_COLD_START)
        self.assertEqual(got.outcomes, ())
        self.assertEqual(got.targets, ())
        self.assertEqual(got.writable, ())
        self.assertEqual(got.age_days, None)
        self.assertTrue(got.blocked_note)

    def test_a_stale_state_still_shows_what_it_would_have_suggested(self):
        """过期不是「算不出来」：格子还在，结论照样算出来，只是不可写。日报要能说
        「若状态仍有效则会有 N 条」——那正是让人知道该去查判定任务的数字。"""
        got = dea.derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)},
            state=dea.RegimeState(regime=DOWNTREND, cycle=BEAR, effective_at=at()),
            strategy_ids=(1,),
            now=at(days=4),
            params=LIFE,
        )
        self.assertEqual(got.blocked, dea.BLOCKED_STALE_STATE)
        self.assertEqual(got.age_days, 4)
        self.assertEqual([o.strategy_id for o in got.targets], [1])
        self.assertEqual(got.writable, ())

    def test_the_note_carries_the_numbers(self):
        """「日志不算被看见」的另一半：这句话要带上能让人动手的数字。"""
        got = dea.derive(
            {},
            state=dea.RegimeState(regime=DOWNTREND, cycle=BEAR, effective_at=at()),
            strategy_ids=(),
            now=at(days=4),
            params=LIFE,
        )
        self.assertIn("4", got.blocked_note)
        self.assertIn("3", got.blocked_note)

    def test_the_staleness_boundary_is_exclusive(self):
        """「过期**超** 3 个自然日」：第 3 天头上还来得及，第 4 天起不再新增。

        边界落在哪一边是会有人依赖的事实（判定任务挂了一次之后还剩几天补救），
        所以逐日钉住，而不是只测「4 天算过期」。
        """
        for age_days, blocked in (
            (0, ""),
            (1, ""),
            (3, ""),
            (4, dea.BLOCKED_STALE_STATE),
        ):
            with self.subTest(age_days=age_days):
                got = dea.derive(
                    {},
                    state=dea.RegimeState(
                        regime=DOWNTREND, cycle=BEAR, effective_at=at()
                    ),
                    strategy_ids=(),
                    now=at(days=age_days),
                    params=LIFE,
                )
                self.assertEqual(got.blocked, blocked)

    def test_a_state_without_an_effective_moment_is_treated_as_cold(self):
        """说不清从哪一刻开始的结论，不拿来停人。"""
        got = dea.derive(
            {},
            state=dea.RegimeState(regime=DOWNTREND, cycle=BEAR, effective_at=None),
            strategy_ids=(),
            now=at(days=99),
            params=LIFE,
        )
        self.assertEqual(got.blocked, dea.BLOCKED_COLD_START)
        self.assertEqual(got.age_days, None)

    def test_a_state_without_a_cycle_direction_blocks_on_no_cell(self):
        """判不出方向（`unclear` / 旧行 `cycle` 为空）→ 没有可问的那一格。

        #66 之后格子键是 cycle，`unclear` 不在三格里。逐策略各落一条 `no_cell` 会把
        一次「判不出方向」摊成几十条噪声，收场与冷启动同形：一条 `blocked`。
        它与冷启动必须**分开**——冷启动是「从没判过」，这里是「判过了，但没判出方向」。
        """
        got = dea.derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)},
            # `state.cell` 会是 None：`unclear` 不是三格之一。
            state=dea.RegimeState(regime=DOWNTREND, cycle="unclear", effective_at=at()),
            strategy_ids=(1,),
            now=at(),
            params=LIFE,
        )
        self.assertEqual(got.blocked, dea.BLOCKED_NO_CELL)
        self.assertEqual(got.outcomes, ())
        self.assertEqual(got.targets, ())
        self.assertEqual(got.writable, ())
        self.assertTrue(got.blocked_note)

    def test_a_legacy_row_without_a_cycle_blocks_the_same_way(self):
        """旧行（两列存在之前写的）`cycle` 是 NULL，与 `unclear` 落到同一档。

        拿 `regime`（四档）去查新代必然落空，所以这是**如实**：那一代对这些日子没有
        结论。把它读成「中性 → 不用停」或「读成某个方向 → 该停」都错。
        """
        got = dea.derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)},
            state=dea.RegimeState(regime=DOWNTREND, effective_at=at()),
            strategy_ids=(1,),
            now=at(),
            params=LIFE,
        )
        self.assertEqual(got.blocked, dea.BLOCKED_NO_CELL)
        self.assertEqual(got.outcomes, ())

    def test_no_note_when_nothing_is_blocked(self):
        got = derive({}, ids=())
        self.assertEqual(got.blocked, "")
        self.assertEqual(got.blocked_note, "")


class TestExemptionAndRunningAreMarks(SimpleTestCase):
    """性质 6 + 豁免：两者都不筛选。"""

    def test_an_exempted_target_is_still_a_target_and_is_listed_as_exempt(self):
        """豁免抑制的是**动作**（第③段的 gate 施加之前要查在期豁免），不是结论。

        所以它照样落一条决策并挂上豁免指针——「已豁免」这句话因此是可查的，
        而不是只活在某一期日报里。
        """
        got = derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)},
            ids=(1,),
            exemptions={(1, BEAR): 77},
        )
        self.assertEqual([o.strategy_id for o in got.targets], [1])
        self.assertEqual([o.strategy_id for o in got.writable], [1])
        self.assertEqual([o.strategy_id for o in got.exempt], [1])
        self.assertEqual(got.targets[0].exempt_id, 77)

    def test_an_exemption_for_another_cell_does_not_apply(self):
        """豁免键是（策略 × 格子）：在别的格被豁免过，不构成这一格的挡箭牌。"""
        got = derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)},
            ids=(1,),
            exemptions={(1, BULL): 77},
        )
        self.assertIsNone(got.targets[0].exempt_id)
        self.assertEqual(got.exempt, ())

    def test_running_is_a_mark_not_a_filter(self):
        """判据二只标记。若它变成筛选，第①段（零执行、可能没有任何活跃会话）
        会天天产出空记录——而每日记录是这一段唯一的产出。"""
        cells = {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT)}
        both = derive(cells, ids=(1, 2), running_ids=(2,))
        self.assertEqual([o.strategy_id for o in both.outcomes], [1, 2])
        by_id = {o.strategy_id: o for o in both.outcomes}
        self.assertFalse(by_id[1].running)
        self.assertTrue(by_id[2].running)
        # 标记不影响建议：没人跑的也知道该停。
        self.assertEqual([o.strategy_id for o in both.targets], [1])

    def test_cell_digest_is_carried_but_not_invented(self):
        """格子上的依据原样带着走；没有格子时是空字典，不是编一份出来。"""
        got = derive(
            {(1, BEAR): cell(1, BEAR, sl.STATE_UNFIT, evidence={"trades": 7})},
            ids=(1, 2),
        )
        self.assertEqual(got.outcomes[0].evidence, {"trades": 7})
        self.assertEqual(got.outcomes[1].evidence, {})

    def test_the_pool_vocabulary_is_carried_through_verbatim(self):
        got = derive(
            {
                (1, BEAR): cell(
                    1,
                    BEAR,
                    sl.STATE_UNFIT,
                    reason=sl.REASON_INSUFFICIENT,
                    source="archetype",
                )
            },
            ids=(1,),
        )
        out = got.outcomes[0]
        self.assertEqual(
            (out.state, out.reason, out.source),
            (sl.STATE_UNFIT, sl.REASON_INSUFFICIENT, "archetype"),
        )
