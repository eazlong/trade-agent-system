"""
Regression: volume_support_reclaim_strategy must not blow up with
'StrategyContext' object has no attribute 'calculate_quantity' on entry.

Failure history: backtest 7bd70c1e-... failed in <8s on
`self.ctx.calculate_quantity(risk_pct=...)`. 被测的是部署位策略文件
`~/.tradelogx/strategies/volume_support_reclaim_strategy.py`，路径见
`_strategy_path()`。
"""

import asyncio
import importlib.util
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from apps.strategy_engine.backtest_mode import BacktestEngine
from apps.strategy_engine.base import StrategyContext


STRATEGY_FILENAME = "volume_support_reclaim_strategy.py"


def _is_file(path: Path) -> bool:
    """`path` 是不是一个普通文件——**把「读不了」当成「不在」**（#58）。

    `Path.is_file()` 在被拒绝访问时会抛 `PermissionError`，不是返回 `False`：
    `pathlib` 不把权限问题归到「不存在」那一类。而本模块的候选里第一条是
    `/root/.tradelogx/strategies/…`——本地开发机上它不存在（`False`，安静），
    GitHub runner 上 `/root` 存在但当前用户读不了（**抛异常**）。

    `PermissionError` 是 `OSError` 的子类，所以一次 `except OSError` 覆盖权限与
    其它 I/O 异常。语义上这是对的：这几条是**候选位置**，探不到就是「这里没有」，
    不是「测试坏了」——真正的「测试坏了」由后面 `is_file()` 为真的分支负责。
    """
    try:
        return path.is_file()
    except OSError:
        return False


def _strategy_path() -> str | None:
    """定位被测策略文件；一个都探不到时给 `None`（**不在模块级调用**，见下）。

    策略的**部署位**是 `~/.tradelogx/strategies/`（见 `apps/skill/loader.py`
    的 `TRADELOGX_ROOT`；容器里 HOME=/root，开发机上是用户家目录）。
    此前的常量写死了 `/root/...`，于是本地开发机上 `_load_strategy()` 静默跳过、
    而 `test_strategy_module_uses_no_missing_context_api` 直接
    `open()` 一个不存在的路径抛 FileNotFoundError。

    这里按与 `test_box_breakout_threshold.py::_strategy_path` 相同的顺序解析：
    显式环境变量覆盖 → 部署位（/root 与当前用户家目录）→ 仓库镜像
    `backend/strategies/`。

    ## 为什么不再在模块级调用（#58）

    原来是 `STRATEGY_PATH = _strategy_path()`——模块级，于是**收集阶段**就执行。
    探测里任何异常都会变成「collection error」，而 pytest 对 collection error 的
    处理是 `Interrupted`：**整个 `pytest apps/` 一条都不跑**。同目录的
    `test_box_breakout_threshold.py` 用的是逐字相同的探测，但它采用懒调用、探不到
    就 `pytest.skip`，所以从来没炸过——差异不在探测本身，在**调用位置**。

    现在三个使用点各自调 `_strategy_path()`，探不到时 skip 本文件的用例。
    """
    override = os.environ.get("VOLUME_RECLAIM_STRATEGY_PATH")
    if override:
        return override if _is_file(Path(override)) else None

    repo_mirror = Path(__file__).resolve().parents[3] / "strategies" / STRATEGY_FILENAME
    candidates = [
        # 部署位优先的意图保留：容器里 HOME=/root，那条路径就是真身。
        # 测试上下文里它探不到时由 `_is_file` 安静地跳过，不炸。
        Path("/root/.tradelogx/strategies") / STRATEGY_FILENAME,
        Path.home() / ".tradelogx" / "strategies" / STRATEGY_FILENAME,
        repo_mirror,
    ]
    for candidate in candidates:
        if _is_file(candidate):
            return str(candidate)
    return None


def _require_strategy_path() -> str:
    """取路径，探不到就 skip 本文件的用例（与兄弟文件的 `pytest.skip` 同一口径）。"""
    path = _strategy_path()
    if path is None:
        pytest.skip(
            f"{STRATEGY_FILENAME} not found in any known strategy dir "
            "(env override / ~/.tradelogx/strategies / backend/strategies)"
        )
    return path


def _load_strategy():
    STRATEGY_PATH = _require_strategy_path()
    spec = importlib.util.spec_from_file_location(
        "volume_support_reclaim_strategy", STRATEGY_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.VolumeSupportReclaimStrategy


def _ctx(balance: Decimal = Decimal("10000")) -> StrategyContext:
    return StrategyContext(
        symbol="BTC/USDT",
        timeframe="1d",
        mode="backtest",
        params={
            "support_period": 7,
            "volume_period": 20,
            "volume_multiplier": 3.0,
            "take_profit_pct": 5.0,
            "stop_loss_pct": 3.0,
            "risk_per_trade_pct": 10.0,
        },
        balance=balance,
    )


def _kline(ts: str, o: float, h: float, l: float, c: float, v: float) -> dict:  # noqa: E741
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_entry_does_not_call_calculate_quantity():
    """Real bug: ctx.calculate_quantity was called and AttributeError killed the backtest."""
    StrategyCls = _load_strategy()
    ctx = _ctx()
    s = StrategyCls(ctx)
    s.ctx.buy = lambda **kw: None  # type: ignore[assignment]
    s.ctx.close_position = lambda **kw: None  # type: ignore[assignment]

    history = [
        _kline(f"2024-01-{i + 1:02d}", 100, 105, 95, 102, 1000.0) for i in range(20)
    ]
    history.append(_kline("2024-01-21", 90, 92, 88, 90, 3000.0))
    current = _kline("2024-01-22", 95, 106, 94, 101, 1000.0)

    # Must not raise AttributeError.
    s.on_bar(current, history)


def test_strategy_module_uses_no_missing_context_api():
    """Source-level guard: no `self.ctx.calculate_quantity` reference anywhere."""
    with open(_require_strategy_path(), encoding="utf-8") as f:
        src = f.read()
    assert "self.ctx.calculate_quantity" not in src, (
        "Strategy references missing StrategyContext.calculate_quantity — backtest will fail."
    )


def test_engine_runs_end_to_end_and_produces_ohlcv_like_equity_curve():
    """End-to-end: BacktestEngine on 120 bars with engineered spring must finish and record equity."""
    StrategyCls = _load_strategy()
    ctx = _ctx()
    strategy = StrategyCls(ctx)

    ohlcv = []
    # 30 stable bars around 100, vol 1000
    for i in range(30):
        ohlcv.append(_kline(f"2024-01-{i + 1:02d}", 100, 105, 95, 100, 1000.0))
    # 1 spike-down bar with 3x volume (breakout candidate)
    ohlcv.append(_kline("2024-02-01", 95, 96, 88, 89, 3000.0))
    # 1 reclaim bar (closes above 95, the support computed from window i-1..i-8)
    ohlcv.append(_kline("2024-02-02", 90, 105, 89, 102, 1000.0))
    # 50 more bars — let take-profit hit
    for i in range(50):
        c = 102 + (i % 5) * 0.2
        ohlcv.append(
            _kline(f"2024-02-{i + 3:02d}", c - 0.5, c + 1.0, c - 1.0, c, 1000.0)
        )
    # pad to 120
    while len(ohlcv) < 120:
        ohlcv.append(
            _kline(f"2024-04-{len(ohlcv) + 1:02d}", 110, 112, 108, 111, 1000.0)
        )

    engine = BacktestEngine(
        strategy=strategy,
        ohlcv_data=ohlcv,
        initial_capital=Decimal("10000"),
        symbol="BTC/USDT",
        timeframe="1d",
    )
    result = asyncio.run(engine.run())
    # Sanity: no exception; stats object returned; equity curve non-empty.
    assert isinstance(result, dict)
    assert len(engine._equity_curve) == len(ohlcv)
    assert any(eq["equity"] != 10000.0 for eq in engine._equity_curve), (
        "Equity never moved — strategy did not trade after the fix."
    )


# --------------------------------------------------------------------------- #
# #58：探针在「读不了的目录」上不许炸，且不许在模块级执行
# --------------------------------------------------------------------------- #


def _deny_root_is_file(monkeypatch):
    """把 `Path.is_file` 桩成 CI 上的真实行为：`/root` 存在但当前用户读不了。

    这不是「模拟一个假想的坏环境」——GitHub runner 以 root 身份跑，`/root` 的权限是
    700 而父目录受保护，于是 `Path("/root/…").is_file()` **抛 `PermissionError`**
    而不是返回 False（本机实测：同一路径在本地返回 False，在 `chmod 000` 的目录下抛）。
    """
    from pathlib import Path as _Path

    real = _Path.is_file

    def fake(self):
        if str(self).startswith("/root/"):
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(_Path, "is_file", fake)


def test_the_probe_treats_a_permission_denied_path_as_absent(monkeypatch):
    """`/root/…` 读不了 ⇒ 当成「这里没有」，继续往后面的候选走，不抛。

    修之前 `_is_file` 就是裸的 `path.is_file()`，于是这一句直接抛
    `PermissionError`；而它又是模块级的，异常发生在 collection 期 → `Interrupted`
    → **整个 `pytest apps/` 一条都不跑**。
    """
    _deny_root_is_file(monkeypatch)
    monkeypatch.delenv("VOLUME_RECLAIM_STRATEGY_PATH", raising=False)

    path = _strategy_path()  # 不许抛

    # 探针要么找到后面的候选（家目录 / 仓库镜像），要么明确给 None。
    assert path is None or os.path.isfile(path)


def test_nothing_is_evaluated_at_import_time():
    """本模块**不得**再有模块级的 `STRATEGY_PATH = _strategy_path()`。

    这一条守的是 #58 的另一半：即使探针本身不再抛，「模块级调用」也会把任何探测异常
    放大成 collection error，代价是整个套件。同目录 `test_box_breakout_threshold.py`
    一直没事，唯一差别就是它把 `_strategy_path()` 放在函数里懒调用。
    """
    import apps.strategy_engine.tests.test_volume_support_reclaim_strategy as module

    assert not hasattr(module, "STRATEGY_PATH"), (
        "模块级 STRATEGY_PATH 又回来了——探测异常会在 collection 期中断整个套件（#58）"
    )


def test_the_guard_reads_the_file_it_actually_loads(monkeypatch):
    """`_load_strategy` 与源码守卫必须用**同一个**解析结果。

    两者原来各读一份 `STRATEGY_PATH` 常量；改成懒调用后如果只改一处，会出现
    「守卫检查的是 A、真正加载的是 B」——那种不一致不会报错，只会让守卫失去意义。
    """
    _deny_root_is_file(monkeypatch)
    monkeypatch.delenv("VOLUME_RECLAIM_STRATEGY_PATH", raising=False)

    import apps.strategy_engine.tests.test_volume_support_reclaim_strategy as module

    seen: list[str] = []
    # 先抓住**真实实现**的引用，再替换模块属性：桩里若按名调用会命中桩自己 → 无限递归。
    real_require = module._require_strategy_path

    def spy():
        path = real_require()
        seen.append(path)
        return path

    monkeypatch.setattr(module, "_require_strategy_path", spy)

    module.test_strategy_module_uses_no_missing_context_api()

    assert seen, "源码守卫没有经由 _require_strategy_path 取路径"
