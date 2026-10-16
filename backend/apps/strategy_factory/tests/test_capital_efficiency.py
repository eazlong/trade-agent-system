"""资金效率**纯计算层**的穷举测试（spec #30 D6 / D7；子票 #33）。

本文件不用 `django_db`：这正是「占用与利用率的算术能在没有 DB 的情况下被穷举」那条要求
的形态（与 `test_walk_forward.py` / `test_evaluate.py` 同一纪律）。取数与落库在 #35 / #38。

钉住的不变式（每条都对应一个能悄悄出错的失效）：

1. **占用是时间加权的，不是期末名义值**——满仓 3 天后清仓的策略不会被算成「占了一半
   资金」，也不会被算成「几乎没用钱」（用户故事 13 的核心用例）。
2. **时间权重不能被抹平**——等权口径下「一笔 3 天 + 一笔 3 天」与「两笔重叠 3 天」会
   得到同一个数，而它们的真实资金压力差一倍。
3. **在仓时长与金额口径分开**——半仓开满全程（时间 100%、金额 50%）与满仓只开一小段
   必须读出不同的风险。
4. **空仓间隙显式计 0**——少了这条，利用率会变成「有仓位时的平均仓位大小」，于是「只开
   1% 时间的满仓策略」与「全程半仓」同数。
5. **窗口左闭右开、区间裁进窗口内**——分子分母必须同源，否则 `time_in_market_pct` 可能
   >100%。
6. **判不了不是判通过，也绝不是 0**——`invalid_capital` / `empty_window` / `no_trades`
   各自整段 None + reason，不抛、不挑默认值。
7. **样本太薄只打标注、不抹数字**（用户故事 14）。
8. **六个键名是跨侧固定契约**——#35 与 #38 写同一组键，改名必须在这里响。
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from apps.strategy_factory import capital_efficiency as ce_module
from apps.strategy_factory.capital_efficiency import (
    CAPITAL_EFFICIENCY_METRICS_KEY,
    CAPITAL_EFFICIENCY_PRECISION,
    CAPITAL_EFFICIENCY_VERSION,
    CapitalEfficiencyReason,
    EfficiencyWindow,
    OccupancyInterval,
    OccupancyIntervalKind,
    capital_efficiency_keys,
    compute_capital_efficiency,
)
from apps.strategy_factory.config import FactoryConfig

# --------------------------------------------------------------------------- #
# 纯度守卫（与 test_walk_forward.py 同一条纪律：算术层不该依赖 Django）
# --------------------------------------------------------------------------- #

FORBIDDEN_IMPORT_ROOTS = ("django", "rest_framework", "celery")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_pure_module_does_not_import_django():
    """资金效率的算术必须能在没有 Django/DB 的情况下被读懂、被测试。

    用 AST 而不是字符串匹配：docstring 里到处在讲 Django 与 `BacktestTrade`，字符串匹配
    会被它们骗过去。

    这一条也把 `session_stats` 挡在外面（它 `from django.db.models import ...`）——本模块
    要同时服务回测侧与会话侧，任何一侧的 Django 依赖进来，另一侧就得为它装环境。
    """
    imports = _imported_modules(Path(ce_module.__file__))
    roots = {name.split(".")[0] for name in imports}
    assert not (roots & set(FORBIDDEN_IMPORT_ROOTS)), (
        f"capital_efficiency.py 引入了 {sorted(roots & set(FORBIDDEN_IMPORT_ROOTS))}"
    )


def test_does_not_import_the_session_side_or_models():
    """纯层不依赖 I/O 层与模型层——否则「零 I/O」这条就只是文档里的一句话。

    `apps.trading.session_stats` 尤其不能进：它 import Django 的 `models` 与
    `DecimalField`，把回测侧也拖进需要 Django 的环境。样本门槛因此取
    `FactoryConfig.min_trades`（同一个数字，但它是纯数据）。
    """
    imports = _imported_modules(Path(ce_module.__file__))
    assert not any("session_stats" in name for name in imports)
    assert not any("models" in name.split(".")[-1:] for name in imports)


# --------------------------------------------------------------------------- #
# 共用夹具
# --------------------------------------------------------------------------- #

#: 基准窗口：10 天。
WINDOW_START = datetime(2026, 3, 1, tzinfo=timezone.utc)
TEN_DAYS = timedelta(days=10)


def _window(start: datetime = WINDOW_START, span: timedelta = TEN_DAYS):
    return EfficiencyWindow(start, start + span)


def _days(start: datetime, n: float) -> timedelta:
    return timedelta(days=n)


CAPITAL = Decimal("10000")


def _held(
    start_offset_days: float,
    duration_days: float,
    notional: Decimal,
    *,
    pnl: Decimal | None = None,
    window_start: datetime = WINDOW_START,
    notes: str = "",
) -> OccupancyInterval:
    """一个「持有着」的占用区间（回测侧的形状）。"""
    return OccupancyInterval(
        start=window_start + _days(window_start, start_offset_days),
        end=window_start + _days(window_start, start_offset_days + duration_days),
        notional=notional,
        pnl=pnl,
        kind=OccupancyIntervalKind.HELD.value,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# 口径 1 / 2：时间加权，不是期末名义值；且权重不能被抹平
# --------------------------------------------------------------------------- #


def test_full_position_three_days_is_not_under_reported():
    """满仓 3 天 / 10 天窗口 → 占用 30%，不是 0 也不是 50%（用户故事 13）。

    期末名义值（已清仓 = 0）会把一个把全部本金压上去的策略读成「几乎没用钱」——恰好把
    高风险读成高效率。等权或「取最大一笔」也不对，见下一条。
    """
    result = compute_capital_efficiency([_held(0, 3, CAPITAL)], _window(), CAPITAL)

    assert result.computed is True
    assert result.avg_capital_used == Decimal("3000")
    assert result.capital_utilization == pytest.approx(30.0)
    assert result.idle_pct == pytest.approx(70.0)
    assert result.time_in_market_pct == pytest.approx(30.0)


def test_full_position_beats_half_position_on_time_weighted_average():
    """满仓 3 天 vs 半仓 10 天：**占用** 30% vs 50%，但**在仓时间** 30% vs 100%。

    这条钉的是「金额口径与时间口径分开」（口径 2）：满仓短打的钱压得更狠（收益率分母更
    小），半仓长持的时间敞口更大。两个数同向或同值都说明有一侧读错了。
    """
    full = compute_capital_efficiency([_held(0, 3, CAPITAL)], _window(), CAPITAL)
    half = compute_capital_efficiency([_held(0, 10, CAPITAL / 2)], _window(), CAPITAL)

    assert full.avg_capital_used < half.avg_capital_used
    assert full.capital_utilization < half.capital_utilization
    # 反过来：时间上满仓只占了 30%，半仓占了全程。
    assert full.time_in_market_pct < half.time_in_market_pct


def test_short_and_long_holds_at_different_notionals_are_not_flattened():
    """短持仓的大额 vs 长持仓的小额：时间加权给出**不同**答案，等权会给出同一个。

    真实占用是 1800（小额占 9 天 + 大额占 1 天）。等权口径算 (1000+9000)/2 = 5000，
    于是「一笔大钱闪一下就撤」与「一笔小钱慢慢磨」被读成同一件事——而它们的资金压力与
    风险完全不同。这条用例专门钉时长权重，等权实现会在这里拿到 50%。
    """
    small_long_big_short = [
        _held(0, 9, CAPITAL / 10),  # 1000 占 9 天
        _held(9, 1, CAPITAL * Decimal("0.9")),  # 9000 只占 1 天
    ]
    result = compute_capital_efficiency(small_long_big_short, _window(), CAPITAL)

    assert result.avg_capital_used == Decimal("1800")
    assert result.capital_utilization == pytest.approx(18.0)
    assert result.time_in_market_pct == pytest.approx(100.0)


def test_swapping_which_leg_holds_longer_changes_the_average():
    """同样两笔、只交换谁持有更久 → 时间加权平均必须改变。

    8200 != 1800。等权口径对这两种排布给出同一个数（(9000+1000)/2），而真相是第二种
    排布在本金上压了 4.5 倍的钱。
    """
    big_long_small_short = [
        _held(0, 9, CAPITAL * Decimal("0.9")),  # 9000 占 9 天
        _held(9, 1, CAPITAL / 10),  # 1000 只占 1 天
    ]
    small_long_big_short = [
        _held(0, 9, CAPITAL / 10),
        _held(9, 1, CAPITAL * Decimal("0.9")),
    ]

    heavy = compute_capital_efficiency(big_long_small_short, _window(), CAPITAL)
    light = compute_capital_efficiency(small_long_big_short, _window(), CAPITAL)

    assert heavy.avg_capital_used == Decimal("8200")
    assert light.avg_capital_used == Decimal("1800")
    assert heavy.capital_utilization > light.capital_utilization * 4


def test_a_single_short_trade_still_has_its_full_weight():
    """单笔短持仓不被低估（用户故事 13 的另一面）。

    一笔只占窗口 1/10 的持仓，它的占用必须按它**真实持续的那一段**进加权，而不是被窗口
    长度稀释成「几乎没占」。
    """
    result = compute_capital_efficiency([_held(0, 1, CAPITAL)], _window(), CAPITAL)

    assert result.avg_capital_used == Decimal("1000")
    assert result.capital_utilization == pytest.approx(10.0)
    assert result.trade_count == 1


def test_longer_hold_at_same_notional_means_more_capital_used():
    """同样满仓、持仓更久 → 时间加权占用更大。时长是权重本身。"""
    short = compute_capital_efficiency([_held(0, 2, CAPITAL)], _window(), CAPITAL)
    long = compute_capital_efficiency([_held(0, 8, CAPITAL)], _window(), CAPITAL)

    assert short.avg_capital_used == Decimal("2000")
    assert long.avg_capital_used == Decimal("8000")
    assert long.capital_utilization > short.capital_utilization


# --------------------------------------------------------------------------- #
# 口径 3：空仓间隙显式计 0
# --------------------------------------------------------------------------- #


def test_idle_gaps_are_counted_as_zero_occupancy_not_skipped():
    """空仓间隙按占用 0、时长照算进分母。

    少了这一条，利用率会变成「有仓位时的平均仓位大小」——于是「只开 10% 时间的满仓策略」
    与「全程半仓」都得到不该出现的那个数。这条用例钉的是分母。
    """
    result = compute_capital_efficiency([_held(0, 1, CAPITAL)], _window(), CAPITAL)
    assert result.window_seconds == pytest.approx(864000.0)  # 10 天
    assert result.occupied_seconds == pytest.approx(86400.0)  # 只有 1 天
    # 分母是**窗口**（10 天），不是 Σ时长（1 天）：占用 = 10000 × 1 / 10 = 1000。
    assert result.avg_capital_used == Decimal("1000")
    assert result.time_in_market_pct == pytest.approx(10.0)


def test_flat_whole_window_means_no_idle():
    """全程持仓 → `idle_pct == 0`、`time_in_market_pct == 100`。"""
    result = compute_capital_efficiency([_held(0, 10, CAPITAL)], _window(), CAPITAL)
    assert result.idle_pct == pytest.approx(0.0)
    assert result.time_in_market_pct == pytest.approx(100.0)
    assert result.capital_utilization == pytest.approx(100.0)


def test_idle_pct_is_the_complement_of_utilization():
    """`idle_pct == 1 - capital_utilization`，两者必须互补。

    它们各自呈现（前端可能只显示一个），互补关系断了就说明有一侧算错了。
    """
    for offset, duration in [(0, 3), (2, 4), (0, 9)]:
        result = compute_capital_efficiency(
            [_held(offset, duration, CAPITAL)], _window(), CAPITAL
        )
        assert result.idle_pct == pytest.approx(100.0 - result.capital_utilization)


# --------------------------------------------------------------------------- #
# 口径 4 / 5：窗口边界归属 + 区间裁进窗口
# --------------------------------------------------------------------------- #


def test_interval_ending_exactly_at_window_end_is_kept_whole():
    """`end == window_end` 的区间整段在窗口内（右端裁剪不砍它）。

    窗口是左闭右开 `[start, end)`：`window_end` 那一瞬属于下一个窗口，但**一直持续到
    `window_end` 的持仓**在窗口内的每一秒都是真实占用。把右端裁成 `< end` 会凭空丢掉最后
    一秒。
    """
    result = compute_capital_efficiency([_held(0, 10, CAPITAL)], _window(), CAPITAL)
    assert result.time_in_market_pct == pytest.approx(100.0)


def test_point_observation_at_window_end_belongs_to_the_next_window():
    """`start == window_end` 的点观测整段在本窗口之外（右开）。

    归属错了会让这一笔同时出现在相邻两个窗口里——窗口是「互不相同的一段行情」的单位，
    一笔算两次就等于给它加权。
    """
    outside = OccupancyInterval(
        start=WINDOW_START + TEN_DAYS,  # 恰在窗口右端
        end=WINDOW_START + TEN_DAYS,
        notional=CAPITAL,
        kind=OccupancyIntervalKind.POINT.value,
    )
    result = compute_capital_efficiency([outside], _window(), CAPITAL)

    assert result.computed is False
    assert result.reason == CapitalEfficiencyReason.NO_TRADES.value
    assert result.trade_count == 0


def test_a_boundary_instant_is_counted_in_exactly_one_adjacent_window():
    """边界那一瞬只归一个窗口：相邻两窗的笔数加起来正好是全部笔数，不多不少。

    这是右开口径最直接的检验。同一个 `t == window_end` 属于后一个窗口（左闭）而不属于
    前一个（右开）——若两侧都算，窗口就不再是「互不相同的一段行情」，一笔会被算两次，
    样本充足度与占用都被悄悄加权。
    """
    boundary = WINDOW_START + TEN_DAYS
    point = OccupancyInterval(
        start=boundary,
        end=boundary,
        notional=CAPITAL,
        kind=OccupancyIntervalKind.POINT.value,
    )
    left = EfficiencyWindow(WINDOW_START, boundary)
    right = EfficiencyWindow(boundary, boundary + TEN_DAYS)

    left_count = compute_capital_efficiency([point], left, CAPITAL).trade_count
    right_count = compute_capital_efficiency([point], right, CAPITAL).trade_count

    assert left_count == 0, "右开：边界瞬不属于左窗"
    assert right_count == 1, "左闭：边界瞬属于右窗"
    assert left_count + right_count == 1, "一个边界瞬只能被数一次"


def test_point_observation_at_window_start_belongs_to_this_window():
    """`start == window_start` 的点观测属于本窗口（左闭）。"""
    at_start = OccupancyInterval(
        start=WINDOW_START,
        end=WINDOW_START,
        notional=CAPITAL,
        kind=OccupancyIntervalKind.POINT.value,
    )
    result = compute_capital_efficiency([at_start], _window(), CAPITAL)

    assert result.computed is True
    assert result.trade_count == 1
    # 点观测权重 0：占用与在仓时间都是 0，但**这一笔被数到了**。
    assert result.avg_capital_used == Decimal("0")
    assert result.time_in_market_pct == pytest.approx(0.0)


def test_interval_opened_before_the_window_is_clipped_to_it():
    """窗口开始前已开仓 → 只计窗口内那一截。

    不裁的话分子取自一个比窗口更大的区间，`time_in_market_pct` 会 >100%。
    """
    carried_in = OccupancyInterval(
        start=WINDOW_START - _days(WINDOW_START, 5),  # 窗口开始前 5 天就开着
        end=WINDOW_START + _days(WINDOW_START, 5),  # 窗口开始后 5 天
        notional=CAPITAL,
        kind=OccupancyIntervalKind.HELD.value,
    )
    result = compute_capital_efficiency([carried_in], _window(), CAPITAL)

    assert result.time_in_market_pct == pytest.approx(50.0)
    assert result.avg_capital_used == Decimal("5000")


def test_interval_closing_after_the_window_is_clipped_to_it():
    """窗口结束后才平仓 → 只计窗口内那一截，`time_in_market_pct` 不超过 100。"""
    carried_out = OccupancyInterval(
        start=WINDOW_START + _days(WINDOW_START, 5),
        end=WINDOW_START + TEN_DAYS + _days(WINDOW_START, 5),
        notional=CAPITAL,
        kind=OccupancyIntervalKind.HELD.value,
    )
    result = compute_capital_efficiency([carried_out], _window(), CAPITAL)

    assert result.time_in_market_pct == pytest.approx(50.0)
    assert result.time_in_market_pct <= 100.0


def test_time_in_market_never_exceeds_one_hundred_percent():
    """跨窗口的大持仓被裁后，在仓占比恒 <= 100%。

    这条是分子分母同源的哨兵：任何一处忘了裁剪都会让它 >100，而一个 >100% 的「在仓时间」
    在任何界面上都看不出是错的。
    """
    intervals = [
        OccupancyInterval(
            start=WINDOW_START - _days(WINDOW_START, 3),
            end=WINDOW_START + TEN_DAYS + _days(WINDOW_START, 3),
            notional=CAPITAL,
        )
    ]
    result = compute_capital_efficiency(intervals, _window(), CAPITAL)
    assert result.time_in_market_pct == pytest.approx(100.0)
    assert result.capital_utilization == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# 口径 5：判不了 —— 整段 None + reason，不抛、不顶替
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "capital",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("-10000"),
        None,
        10000.0,  # float：这条链路上钱是 Decimal
        True,  # bool 不是金额
    ],
)
def test_invalid_capital_is_unresolvable_not_zero(capital):
    """本金 <=0 / 不可用 → 整段 None + `invalid_capital`，**不抛、不给 0**。

    `0` 会被读成「完全没用钱」（于是「效率真高」），而真相是「这个实验的分母不成立」。
    与 #26 修掉的 NaN fail-open 同一纪律：钱相关的判不了必须是「什么都不发生」。
    """
    result = compute_capital_efficiency([_held(0, 3, CAPITAL)], _window(), capital)

    assert result.computed is False
    assert result.reason == CapitalEfficiencyReason.INVALID_CAPITAL.value
    # 整段 None，不是某一个键 None。
    assert result.avg_capital_used is None
    assert result.capital_utilization is None
    assert result.idle_pct is None
    assert result.time_in_market_pct is None
    assert result.return_per_capital_used_pct is None


@pytest.mark.parametrize(
    "capital", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_non_finite_capital_is_unresolvable(capital):
    """NaN / ±inf 本金 → 判不了。

    `Decimal("NaN") <= 0` 是 **False**，所以必须先判有限性：少了这一步，NaN 本金会让
    `capital_utilization > 1` 静默为假，于是「利用率超标」看不出来。
    """
    result = compute_capital_efficiency([_held(0, 3, CAPITAL)], _window(), capital)
    assert result.computed is False
    assert result.reason == CapitalEfficiencyReason.INVALID_CAPITAL.value


def test_empty_window_is_unresolvable():
    """空窗（`end <= start`）→ `empty_window`，不是除零也不是 0。"""
    zero_length = EfficiencyWindow(WINDOW_START, WINDOW_START)
    result = compute_capital_efficiency([_held(0, 3, CAPITAL)], zero_length, CAPITAL)

    assert result.computed is False
    assert result.reason == CapitalEfficiencyReason.EMPTY_WINDOW.value
    assert result.avg_capital_used is None
    assert result.time_in_market_pct is None


def test_no_trades_is_unresolvable_not_zero():
    """零笔数 → `no_trades`，**绝不是 0**。

    0 会被读成「完全没用钱」，于是「效率真高」，而真相是「这一段什么都没发生」。
    """
    result = compute_capital_efficiency([], _window(), CAPITAL)

    assert result.computed is False
    assert result.reason == CapitalEfficiencyReason.NO_TRADES.value
    assert result.avg_capital_used is None
    assert result.capital_utilization is None
    assert result.trade_count == 0


def test_trades_outside_the_window_are_no_trades():
    """给了行、但一行都不落在窗口内 → `no_trades`。

    它们不改变窗口里任何一秒的资金状态，却会让笔数与样本标注失真。
    """
    before = _held(0, 3, CAPITAL, window_start=WINDOW_START - _days(WINDOW_START, 30))
    after = _held(0, 3, CAPITAL, window_start=WINDOW_START + _days(WINDOW_START, 30))

    for interval in (before, after):
        result = compute_capital_efficiency([interval], _window(), CAPITAL)
        assert result.computed is False
        assert result.reason == CapitalEfficiencyReason.NO_TRADES.value


def test_unknown_notional_is_unresolvable_not_zero():
    """开着仓位但占用金额不可知 → 判不了，**不是 0**。

    把 `None` 当 0 会让加权平均被系统性低估，而「占用更多钱」≠「效率更高」这件事会因此
    反过来——一个金额都没记全的实验会看起来比谁都干净。
    """
    unknown = OccupancyInterval(
        start=WINDOW_START,
        end=WINDOW_START + _days(WINDOW_START, 3),
        notional=None,
        kind=OccupancyIntervalKind.HELD.value,
    )
    result = compute_capital_efficiency([unknown], _window(), CAPITAL)

    assert result.computed is False
    assert result.reason == CapitalEfficiencyReason.INVALID_CAPITAL.value
    assert result.avg_capital_used is None


def test_three_reasons_are_distinguishable():
    """三个原因码必须分得开——它们要人做的事不同。

    `invalid_capital` 该修本金，`empty_window` 该修窗口边界，`no_trades` 该补时间。
    混成一个码会让归因变成猜。
    """
    reasons = {
        compute_capital_efficiency(
            [_held(0, 3, CAPITAL)], _window(), Decimal("0")
        ).reason,
        compute_capital_efficiency(
            [_held(0, 3, CAPITAL)],
            EfficiencyWindow(WINDOW_START, WINDOW_START),
            CAPITAL,
        ).reason,
        compute_capital_efficiency([], _window(), CAPITAL).reason,
    }
    assert reasons == {
        CapitalEfficiencyReason.INVALID_CAPITAL.value,
        CapitalEfficiencyReason.EMPTY_WINDOW.value,
        CapitalEfficiencyReason.NO_TRADES.value,
    }


def test_unresolvable_never_raises():
    """判不了的门**不抛异常**——「数据不够」是一个结论，不是一次崩溃。"""
    # 每一种坏输入都要能拿到一个结论对象，而不是一个 traceback。
    for capital in (None, Decimal("0"), Decimal("NaN"), 0.0):
        result = compute_capital_efficiency([_held(0, 3, CAPITAL)], _window(), capital)
        assert isinstance(result, ce_module.CapitalEfficiencyResult)


def test_inverted_interval_is_rejected_at_construction():
    """`end < start` 是构造错误，当场 ValueError（不是被静默算成一段）。"""
    with pytest.raises(ValueError):
        OccupancyInterval(
            start=WINDOW_START + _days(WINDOW_START, 3),
            end=WINDOW_START,
            notional=CAPITAL,
        )


def test_naive_timestamps_are_rejected_at_construction():
    """naive 时间戳当场拒绝：naive 交给 `.astimezone()` 会按**进程 TZ** 解释。

    口径挂在别人的副作用上（与 `slicing._as_utc` 同一条纪律，这里显式钉死）。
    """
    naive = datetime(2026, 3, 1)  # 无 tzinfo
    with pytest.raises(ValueError):
        OccupancyInterval(start=naive, end=naive, notional=CAPITAL)
    with pytest.raises(ValueError):
        EfficiencyWindow(naive, naive)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_notional_is_rejected_at_construction(bad):
    """NaN / ±inf 占用金额当场拒绝：它会让任何比较静默为假。"""
    with pytest.raises(ValueError):
        OccupancyInterval(
            start=WINDOW_START,
            end=WINDOW_START + _days(WINDOW_START, 3),
            notional=bad,
        )


# --------------------------------------------------------------------------- #
# 口径 6：样本太薄只打标注，不抹数字（用户故事 14）
# --------------------------------------------------------------------------- #


def test_thin_sample_still_computes_every_metric_but_flags_itself():
    """2 笔交易：数字照算，`sample_sufficient=False`（用户故事 14）。

    反过来（因为样本薄就把数字抹成 `None`）会把「有 2 笔、每笔都满仓」这个真实观察删掉，
    而它恰恰是判断「要不要再跑久一点」的起点。
    """
    result = compute_capital_efficiency(
        [
            _held(0, 3, CAPITAL, pnl=Decimal("100")),
            _held(4, 3, CAPITAL, pnl=Decimal("50")),
        ],
        _window(),
        CAPITAL,
    )

    assert result.computed is True
    assert result.trade_count == 2
    assert result.sample_sufficient is False
    assert result.sample_threshold == 30
    # 数字一个不少。
    assert result.avg_capital_used is not None
    assert result.capital_utilization is not None
    assert result.idle_pct is not None
    assert result.time_in_market_pct is not None
    assert result.return_per_capital_used_pct is not None


def test_sample_sufficiency_flips_at_the_threshold():
    """门槛两侧：29 笔不足、30 笔够。

    门槛走配置面（`FactoryConfig.min_trades`），所以这条同时钉住「改配置会改判定」。
    """
    cfg = FactoryConfig(min_trades=30)
    win = _window()

    def _with_n_trades(n: int):
        # 每笔各占窗口的 1/n，错开排布，保证都落在窗口内。
        return [
            OccupancyInterval(
                start=WINDOW_START + timedelta(days=10 * i / n),
                end=WINDOW_START + timedelta(days=10 * (i + 1) / n),
                notional=CAPITAL,
            )
            for i in range(n)
        ]

    thin = compute_capital_efficiency(_with_n_trades(29), win, CAPITAL, config=cfg)
    enough = compute_capital_efficiency(_with_n_trades(30), win, CAPITAL, config=cfg)

    assert thin.sample_sufficient is False
    assert enough.sample_sufficient is True
    assert enough.trade_count == 30


def test_sample_threshold_comes_from_config():
    """门槛缺省取 `FactoryConfig.min_trades`——默认值只有一处。

    若这里写死 30，将来 `min_trades` 改了，本模块会与漏斗其余部分用两套门槛。
    """
    assert (
        compute_capital_efficiency(
            [_held(0, 3, CAPITAL)], _window(), CAPITAL
        ).sample_threshold
        == FactoryConfig().min_trades
    )

    cfg = FactoryConfig(min_trades=5)
    assert (
        compute_capital_efficiency(
            [_held(0, 3, CAPITAL)], _window(), CAPITAL, config=cfg
        ).sample_threshold
        == 5
    )


def test_explicit_sample_threshold_is_validated():
    """显式传的门槛也要守边界：不能只依赖配置面的 `__post_init__`。"""
    with pytest.raises(ValueError):
        compute_capital_efficiency(
            [_held(0, 3, CAPITAL)], _window(), CAPITAL, sample_threshold=-1
        )
    with pytest.raises(ValueError):
        compute_capital_efficiency(
            [_held(0, 3, CAPITAL)], _window(), CAPITAL, sample_threshold=True
        )


def test_unresolvable_result_does_not_claim_sufficiency():
    """判不了时 `sample_sufficient=False`：不把「证据不足」读成「样本够」。

    样本标注与「判不了」是两件事，混起来会让一个只有 2 笔的窗口看起来样本充足。
    """
    result = compute_capital_efficiency([], _window(), CAPITAL)
    assert result.computed is False
    assert result.sample_sufficient is False


# --------------------------------------------------------------------------- #
# 口径 6：单位占用收益
# --------------------------------------------------------------------------- #


def test_return_per_capital_used_uses_the_time_weighted_average():
    """`return_per_capital_used_pct = 总盈亏 / avg_capital_used`。

    分子分母必须同源（同一个加权平均）。拿期末资金或最大一笔当分母会让「单位占用的收益」
    变成另一个量，而它在界面上与资金效率并列，看不出换了分母。
    """
    # 占用 3000、总盈亏 600 → 20%。
    result = compute_capital_efficiency(
        [_held(0, 3, CAPITAL, pnl=Decimal("600"))], _window(), CAPITAL
    )
    assert result.avg_capital_used == Decimal("3000")
    assert result.return_per_capital_used_pct == pytest.approx(20.0)


def test_same_pnl_over_more_capital_used_gives_lower_efficiency():
    """同样的盈亏、占用更多 → 单位占用收益更低。

    这条钉的是分子/分母没被调换：调换后「占用更多」会读出「效率更高」，与金融常识相反。
    """
    light = compute_capital_efficiency(
        [_held(0, 3, CAPITAL / 10, pnl=Decimal("600"))], _window(), CAPITAL
    )
    heavy = compute_capital_efficiency(
        [_held(0, 10, CAPITAL, pnl=Decimal("600"))], _window(), CAPITAL
    )

    assert light.avg_capital_used < heavy.avg_capital_used
    assert light.return_per_capital_used_pct > heavy.return_per_capital_used_pct


def test_pnl_is_summed_across_intervals():
    """总盈亏是各笔相加（口径：一段窗口一个总盈亏）。"""
    result = compute_capital_efficiency(
        [
            _held(0, 5, CAPITAL, pnl=Decimal("100")),
            _held(5, 5, CAPITAL, pnl=Decimal("250")),
        ],
        _window(),
        CAPITAL,
    )
    assert result.pnl_total == Decimal("350")
    # 占用全程满仓 → 10000；350 / 10000 = 3.5%。
    assert result.return_per_capital_used_pct == pytest.approx(3.5)


def test_explicit_pnl_total_wins_over_per_interval_sum():
    """显式传入的总盈亏优先于逐笔相加。

    调用方可能已经有一个汇总口径（#38 会有），两边各自加一遍就有两个可能不一致的答案。
    """
    result = compute_capital_efficiency(
        [_held(0, 3, CAPITAL, pnl=Decimal("600"))],
        _window(),
        CAPITAL,
        pnl_total=Decimal("300"),
    )
    assert result.pnl_total == Decimal("300")
    assert result.return_per_capital_used_pct == pytest.approx(10.0)


def test_missing_pnl_yields_numbers_but_no_return_metric():
    """没结算的持仓仍然占着钱：效率算得出，只是 `return_per_capital_used_pct` 为 `None`。

    把整段判成 `None` 会丢掉「这段时间占了多少资金」这个已经确定的观察。
    """
    result = compute_capital_efficiency([_held(0, 3, CAPITAL)], _window(), CAPITAL)
    assert result.computed is True
    assert result.avg_capital_used == Decimal("3000")
    assert result.return_per_capital_used_pct is None
    assert result.pnl_total is None


# --------------------------------------------------------------------------- #
# 精度与确定性
# --------------------------------------------------------------------------- #


def test_precision_is_declared_and_reasonable():
    assert isinstance(CAPITAL_EFFICIENCY_PRECISION, int)
    assert 0 < CAPITAL_EFFICIENCY_PRECISION <= 12


def test_repeating_the_computation_gives_bit_identical_numbers():
    """同样的输入 → 同样的数（定精度的理由：可复现，不是一次浮点抽签）。"""
    intervals = [
        _held(1, 3, CAPITAL, pnl=Decimal("137.13")),
        _held(6, 2, CAPITAL / 3),
    ]
    first = compute_capital_efficiency(intervals, _window(), CAPITAL)
    second = compute_capital_efficiency(intervals, _window(), CAPITAL)

    assert first == second
    assert json.dumps(first.as_payload(), sort_keys=True) == json.dumps(
        second.as_payload(), sort_keys=True
    )


def test_percentages_are_rounded_to_the_declared_precision():
    """百分比按声明的精度落，不多出浮点尾巴。

    一个会随时钟或调用顺序漂的尾数，在界面上会被读成「两次算出来不一样」。
    """
    # 1/3 天落在 10 天窗口上 → 在仓时间占比 33.3333...%
    result = compute_capital_efficiency([_held(0, 10 / 3, CAPITAL)], _window(), CAPITAL)
    assert result.time_in_market_pct == pytest.approx(33.333333, abs=1e-6)
    text = repr(result.time_in_market_pct)
    assert "3333333333" not in text, "百分比没定精度"


def test_payload_is_json_safe():
    """载荷要能原样进 `JSONField`。"""
    result = compute_capital_efficiency(
        [_held(0, 3, CAPITAL, pnl=Decimal("137.13"))], _window(), CAPITAL
    )
    payload = result.as_payload()
    assert json.loads(json.dumps(payload, sort_keys=True)) == payload


def test_money_is_a_fixed_point_string_not_scientific_notation():
    """金额出成定点串：`Decimal` 的 `str()` 会给 `"0E-8"` 这类科学计数法。

    它在 JS 侧恰好还能被 `parseFloat` 读成 0，所以这个错不会以「显示不对」的形式暴露，
    只会以「接口里躺着一个怪字符串」的形式留着（与 `session_stats.money()` 同一条纪律）。
    """
    result = compute_capital_efficiency(
        [_held(0, 3, Decimal("0"), pnl=Decimal("0"))], _window(), CAPITAL
    )
    payload = result.as_payload()
    assert payload["avg_capital_used"] == "0.00000000"
    assert "E" not in payload["avg_capital_used"].upper()


# --------------------------------------------------------------------------- #
# 跨侧契约（为 #35 / #38 铺路）
# --------------------------------------------------------------------------- #


def test_the_six_metric_keys_are_the_fixed_contract():
    """spec D6 的六个键一个不少、一个不多（用户故事 22 的同一套口径）。

    #35 的回测侧与 #38 的会话侧写的是同一组键。改名必须在这里响——两边各自
    `payload[...]` 取用时，没有任何东西会因为改名而报错。
    """
    assert capital_efficiency_keys() == (
        "avg_capital_used",
        "capital_utilization",
        "idle_pct",
        "time_in_market_pct",
        "return_per_capital_used_pct",
        "sample_sufficient",
    )


def test_payload_carries_every_contract_key():
    """载荷必须包含那六个键——少了任何一个，消费侧只能自己挑一个默认值。"""
    result = compute_capital_efficiency(
        [_held(0, 3, CAPITAL, pnl=Decimal("100"))], _window(), CAPITAL
    )
    payload = result.as_payload()
    missing = set(capital_efficiency_keys()) - set(payload)
    assert not missing, f"载荷缺键：{sorted(missing)}"


def test_unresolvable_payload_still_carries_every_contract_key():
    """判不了时六个键也在（值为 `None`）：消费侧不必判「键在不在」。

    只判 `None` 就够了——「键缺失」与「值为 None」混着出现，会让前端写出两套分支。
    """
    payload = compute_capital_efficiency([], _window(), CAPITAL).as_payload()
    for key in capital_efficiency_keys():
        assert key in payload, f"判不了的载荷缺 {key}"


def test_metrics_key_and_version_are_pinned():
    """`metrics["capital_efficiency"]` 的键名与载荷版本是跨模块契约。"""
    assert CAPITAL_EFFICIENCY_METRICS_KEY == "capital_efficiency"
    assert CAPITAL_EFFICIENCY_VERSION == 1


# --------------------------------------------------------------------------- #
# 回测侧 vs 会话侧的不对称（本票必须写明的那个边界）
# --------------------------------------------------------------------------- #


def test_backtest_side_shape_produces_real_time_weighted_metrics():
    """回测侧的形状（真开平仓时刻对）→ 时间加权全部成立。"""
    backtest_shaped = [
        OccupancyInterval(
            start=datetime(2026, 3, 1, 8, tzinfo=timezone.utc),
            end=datetime(2026, 3, 4, 12, tzinfo=timezone.utc),
            notional=Decimal("10000"),
            pnl=Decimal("420"),
            kind=OccupancyIntervalKind.HELD.value,
            notes="BacktestTrade close 行：entry_time / exit_time",
        )
    ]
    result = compute_capital_efficiency(backtest_shaped, _window(), CAPITAL)

    assert result.computed is True
    assert result.time_in_market_pct > 0
    assert result.avg_capital_used > 0
    assert result.notes == ("BacktestTrade close 行：entry_time / exit_time",)


def test_session_side_point_observations_are_counted_but_weight_zero():
    """会话侧只能给点观测：它们**计入笔数**、对时间权重贡献 0。

    这是本票对「两边不对称」的决议：不假装会话侧的 `time_in_market_pct` 与回测侧同义，
    也不静默地产出一个看起来一样的数。
    """
    session_shaped = [
        OccupancyInterval(
            start=WINDOW_START + timedelta(days=i),
            end=WINDOW_START + timedelta(days=i),
            notional=Decimal("250"),
            pnl=Decimal("12.5"),
            kind=OccupancyIntervalKind.POINT.value,
            notes="Order 平仓行的时刻：无成对开平仓时刻，点观测",
        )
        for i in range(3)
    ]
    result = compute_capital_efficiency(session_shaped, _window(), CAPITAL)

    assert result.computed is True
    assert result.trade_count == 3  # 笔数照算
    assert result.time_in_market_pct == pytest.approx(0.0)  # 时间权重为 0
    assert result.avg_capital_used == Decimal("0")
    # 单位占用收益除不出来（分母 0）→ None，而不是 inf 或 0。
    assert result.return_per_capital_used_pct is None
    # 每一笔都留下「这是点观测」的痕迹，消费方据此能说「会话侧不可与回测侧同读」。
    assert len(result.notes) == 6  # 3 笔 × （点观测 + 自带 notes）


def test_point_observations_declare_their_coarser_semantics():
    """`kind=POINT` 必须在载荷里可分辨——否则两边的 `time_in_market_pct` 会同名不同义。"""
    assert OccupancyIntervalKind.values() == ["held", "point"]
    held = OccupancyInterval(
        start=WINDOW_START,
        end=WINDOW_START + _days(WINDOW_START, 1),
        notional=CAPITAL,
        kind=OccupancyIntervalKind.HELD.value,
    )
    point = OccupancyInterval(
        start=WINDOW_START,
        end=WINDOW_START,
        notional=CAPITAL,
        kind=OccupancyIntervalKind.POINT.value,
    )
    held_result = compute_capital_efficiency([held], _window(), CAPITAL)
    point_result = compute_capital_efficiency([point], _window(), CAPITAL)

    assert held_result.time_in_market_pct > 0
    assert point_result.time_in_market_pct == 0
    # 两个载荷都还在同一组键上（同一套公式），差的是输入能提供多少信息。
    assert set(held_result.as_payload()) == set(point_result.as_payload())
