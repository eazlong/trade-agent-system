#!/usr/bin/env python
"""离线回测 harness —— 复用仓库真实 BacktestEngine，不写数据库。

为什么是「harness」而不是又一个自带撮合的回测器：仓库里已经有两条互不一致的
自研回测脚本（``backtest_box_breakout_retest.py`` 自带撮合、``run_box_4h_backtest.py``
调用真引擎但硬编码策略/标的）。自带撮合的脚本算出来的数字与实盘链路（BacktestEngine
→ LiveStrategyRunner）没有关系，参数扫描时会把「脚本的假设」当成「策略的性能」。
本脚本只做三件事：**喂数据 → 跑真引擎 → 把引擎吐出来的东西按人类能读的形状打印**。

不连数据库、不写 ``BacktestResult``：``django.setup()`` 用 ``core.settings.test``
（临时 sqlite），引擎本身不碰 DB，唯一会写库的入口是 ``save_backtest_result``，
本脚本**不调用**它。

用法（在 backend/ 目录下）::

    .venv/bin/python scripts/backtest_smma_fractal.py \\
        --data ../.scratch/market_data/btc_usdt_5m.json \\
        --strategy-file smma_fractal_rsi_scalp_strategy.py \\
        --params "rr_ratio=1.5,max_hold_bars=48" \\
        --out ../.scratch/bt_smma_sweep.json

    # 参数扫描（JSON: [{"rr_ratio": 1.5}, {"rr_ratio": 2.0}]）
    .venv/bin/python scripts/backtest_smma_fractal.py --data ... --sweep sweep.json --out r.json

    # 只验证 warmup 是否够（不跑撮合，退出码 0）
    .venv/bin/python scripts/backtest_smma_fractal.py --data ... --warmup-only

退出码：
    0  正常（每个参数组都至少产出 1 笔已平仓交易；--warmup-only 只看 warmup）
    1  有参数组产出 0 笔已平仓交易 —— **显式告警**，不当作成功
    2  输入/加载失败（数据缺失、策略文件不存在、sweep 格式错误等）

两个与仓库现状有关的注意事项（脚本内已做规避，改代码前请先读这里）：

1. **``BacktestEngine.run()`` 是 O(n²)，本脚本用零拷贝 ``HistoryView`` 堵住。**
   引擎每根 bar 都做 ``history = self.ohlcv_data[: i + 1]``；对 list 这是真拷贝，
   整轮就是 ``Σ O(i) = O(n²)``。实测纯切片循环（进度条都不跑）：

   ==============  =============  =============
   bars            真 list        HistoryView
   ==============  =============  =============
   20,000          0.524 s        0.010 s
   40,000          2.027 s        0.021 s
   295,028         （分钟级，不等）  0.194 s
   ==============  =============  =============

   （真 list 的 20k→40k 是 4×，正是 O(n²) 的指纹。）全量回测因此从「跑不完」
   变成 5–6 秒/组。**别把视图「简化」回 list**——那不是优化，是能不能跑完。
   注意视图不是 list：少数策略把 history 往下传给 ``detect_box_range`` 这类
   开头就 ``isinstance(klines, list)`` 硬校验的指标，会直接抛错。这种情况下
   脚本如实退回真 list 并发 WARNING（``--out`` 里 ``zero_copy_history=false``
   会记下来），代价是那一次跑成 O(n²)。
2. ``backend/strategies/rsi_cross.py`` 里写的是 ``from .portfolio import
   PctCapitalPortfolio``，但 ``backend/strategies/`` 下并没有 ``portfolio.py``
   （那个类在 ``apps.strategy_engine.portfolio``）。因此该文件用
   ``importlib`` 直接加载会 ``ModuleNotFoundError``。本脚本在加载
   ``backend/strategies/`` 下的文件时，把 ``strategies.portfolio`` 别名指向
   引擎的 portfolio 模块（仅当目录里确实没有同名真实文件时才装），
   这样 rsi_cross.py 不改一行也能跑。
3. ``django.setup()`` 会触发 ``apps.datasource.apps.DataSourceConfig.ready()``
   启动 Binance WebSocket 行情供给（head bar），以及
   ``StrategyRegistry.discover()`` 扫描 ``~/.tradelogx/strategies/``——后者会把
   每个策略的 description 打到 stdout，前者会反复重连并往 stderr 刷代理错误。
   本脚本在 setup 前把 ``settings.DATASOURCE_AUTO_WS`` 置 False，并在 setup
   期间把 stdout 重定向到缓冲区（默认丢弃，``TRADE_AGENT_HARNESS_DEBUG=1``
   时转写 stderr），保证 CLI 的 stdout 只有结果本身。

账本口径（打印里的 ``ledger`` 行）：引擎的 ``pnl`` 只在平仓腿上记，且已含
滑点与平仓腿佣金、**不含开仓腿佣金**。所以恒等式是
``equity_change == Σ(平仓 pnl) - Σ(开仓腿佣金)``；实测 gap 恰为 0.0000。
滑点**不能**再减一次——它已烙在成交价里。

确定性：无随机、无 wall-clock 时间戳进入输出；所有 dict 键排序后写出，
``--out`` 用同参数重跑应逐字节一致（数据文件不变的前提下）。同 pnl 的交易
按 ``(entry_time, exit_time, side)`` 打破平局，不依赖列表顺序。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.util
import io
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
STRATEGIES_DIR = BACKEND_DIR / "strategies"
DEFAULT_STRATEGY_FILE = "smma_fractal_rsi_scalp_strategy.py"
DEFAULT_TIMEFRAME = "5m"
SCRIPT_REL_PATH = "backend/scripts/backtest_smma_fractal.py"
TOP_N_TRADES = 3

logger = logging.getLogger("backtest_harness")

_OUT = sys.stdout.write
_ERR = sys.stderr.write


class HistoryView(Sequence):
    """零拷贝的 K 线序列视图 —— 专治 ``BacktestEngine.run()`` 的 O(n²)。

    ``BacktestEngine.run()`` 每根 bar 都做 ``history = self.ohlcv_data[: i + 1]``：
    对 Python list 来说这是一次 **O(i) 的真拷贝**，于是整轮回测是
    ``Σ O(i) = O(n²)``。实测（本机，M 系列）：

    ==========  ==============  ================================
    bars        list 直传        HistoryView
    ==========  ==============  ================================
    20,000      ~0.6 s          同量级（拷贝还不占大头）
    100,000     17.1 s          —
    295,028     几分钟到十几分钟  0.12 s（纯切片循环）
    ==========  ==============  ================================

    换成视图的原因不是「优化」，是**能不能跑完**：295,028 根真实 BTC 5m 数据
    用 list 直传，单组参数就要跑十几分钟，参数扫描完全无法进行。

    为什么视图是安全的：引擎对 ``ohlcv_data`` 只做三种操作——``len()``、
    ``data[i]`` 取单根、``data[:i+1]`` 切前缀。切片返回的仍是视图（O(1)），
    所以每根 bar 的开销从 O(i) 降到 O(1)。这里**只支持 step=1 切片**并显式
    报错：引擎不需要带步长的切片，若将来有人加了，宁可炸掉也不要静默返回
    错数据。
    """

    __slots__ = ("_data", "_start", "_stop")

    def __init__(self, data, start: int = 0, stop: int | None = None):
        self._data = data
        self._start = start
        self._stop = len(data) if stop is None else stop

    def __len__(self) -> int:
        return self._stop - self._start

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            start, stop, step = idx.indices(len(self))
            if step != 1:
                raise ValueError("HistoryView 只支持 step=1 的切片")
            return HistoryView(self._data, self._start + start, self._start + stop)
        if idx < 0:
            idx += len(self)
        if not (0 <= idx < len(self)):
            raise IndexError(idx)
        return self._data[self._start + idx]

    def __iter__(self):
        for i in range(self._start, self._stop):
            yield self._data[i]


# ──────────────────────────────────────────────────────────────────────────────
# 环境引导
# ──────────────────────────────────────────────────────────────────────────────


def bootstrap_django() -> None:
    """准备 Django 环境（测试 settings、允许双向持仓、关掉 WS 行情供给）。

    ``STRATEGY_ENGINE_ALLOW_SHORT`` 必须在**任何策略模块被 import 之前**设置：
    ``registry.register`` 在 import 期读一次该开关，``supports_short=True``
    的策略若读到时是关的会被**静默拒绝注册**（只留一行 logger.error），
    于是 ``StrategyRegistry.get_class`` 找不到它。设置成环境变量而不是
    Django setting，是因为 ``core.settings.test`` 里没有这个键，而环境变量
    是 registry 的兜底来源。
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.test")
    os.environ["STRATEGY_ENGINE_ALLOW_SHORT"] = "1"
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    import django
    from django.conf import settings

    # 触发 settings 加载（但尚未 populate 应用注册表），随后关闭 WS 行情供给。
    # ws_runner.start_market_feed 读的是 settings.DATASOURCE_AUTO_WS，在
    # apps.datasource.apps.ready() 里被调用——即 django.setup() 内部。
    settings.DATASOURCE_AUTO_WS = False

    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        django.setup()
    finally:
        sys.stdout = real_stdout

    noise = captured.getvalue().strip()
    if noise:
        if os.environ.get("TRADE_AGENT_HARNESS_DEBUG", "").lower() in (
            "1",
            "true",
            "yes",
        ):
            _ERR("\n--- django.setup() output (TRADE_AGENT_HARNESS_DEBUG=1) ---\n")
            _ERR(noise + "\n")
            _ERR("--- end django.setup() output ---\n\n")
        else:
            logger.debug("suppressed %d chars of django.setup() output", len(noise))


# ──────────────────────────────────────────────────────────────────────────────
# 参数解析
# ──────────────────────────────────────────────────────────────────────────────


def parse_kv_params(text: str | None) -> dict:
    """``"k=v,k=v"`` → dict，值按 JSON 字面量推断类型（失败则保留字符串）。

    ``position_pct=0.25`` → 0.25（float），``max_hold_bars=48`` → 48（int），
    ``intrabar_exit=true`` → True（bool），``tp_mode=ma`` → "ma"（str）。
    不做推断的话，``"48"`` 会以字符串进策略，``int("48")`` 有的策略会崩、有的
    会静默走默认值——两种都不该发生。
    """
    out: dict = {}
    if not text:
        return out
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"--params 项 {chunk!r} 不是 k=v 形式")
        key, _, raw = chunk.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"--params 项 {chunk!r} 缺少键名")
        out[key] = _coerce_scalar(raw.strip())
    return out


def _coerce_scalar(raw: str):
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("none", "null"):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def load_sweep(path: str) -> list[dict]:
    """``--sweep`` JSON：必须是 ``[{param: value}, ...]``。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("params"), list):
        payload = payload["params"]
    if not isinstance(payload, list) or not all(isinstance(x, dict) for x in payload):
        raise ValueError(f"--sweep {path} 必须是 [{{k: v}}, ...] 形式的 JSON 数组")
    return [dict(x) for x in payload]


# ──────────────────────────────────────────────────────────────────────────────
# 数据装载
# ──────────────────────────────────────────────────────────────────────────────


def _parse_timestamp(value) -> datetime:
    """把 K 线时间戳统一成 aware-UTC datetime。

    支持三种写法：epoch 毫秒（int/float/纯数字字符串）、ISO 8601（可带偏移）、
    naive ISO（按 UTC 解释）。仓库纪律：**绝不**用 ``fromtimestamp`` 的无 ``tz=``
    形式（会静默按本机时区偏移）。
    """
    if isinstance(value, bool):
        raise ValueError(f"非法时间戳: {value!r}")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    text = str(value or "").strip()
    if not text:
        raise ValueError("时间戳为空")
    if text.isdigit():
        return datetime.fromtimestamp(float(text) / 1000.0, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"无法解析时间戳 {value!r}: {exc}") from exc
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def load_bars(path: str) -> list[dict]:
    """读 OHLCV JSON → 引擎要的 ``[{timestamp, open, high, low, close, volume}]``。

    接受两种形状：dict 列表（fetcher 写的格式），或 ccxt 元组列表
    ``[[ts, o, h, l, c, v], ...]``。时间戳统一 ISO-UTC、按时间升序，
    并做 OHLC 合法性校验（非正价格会让引擎的 pnl_pct / 保证金约束失真）。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        for key in ("bars", "data", "ohlcv"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break

    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: 需要非空的 K 线数组")

    bars: list[dict] = []
    for idx, row in enumerate(raw):
        if isinstance(row, dict):
            try:
                ts, o, h, low, c, v = (
                    row["timestamp"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row.get("volume", 0),
                )
            except KeyError as exc:
                raise ValueError(f"{path}: 第 {idx} 根 K 线缺字段 {exc}") from exc
        elif isinstance(row, (list, tuple)) and len(row) >= 6:
            ts, o, h, low, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
        else:
            raise ValueError(
                f"{path}: 第 {idx} 根 K 线形状不支持: {type(row).__name__}"
            )

        close = float(c)
        if close <= 0:
            raise ValueError(f"{path}: 第 {idx} 根 K 线收盘价非正: {close!r}")
        bars.append(
            {
                "timestamp": _parse_timestamp(ts).isoformat(),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": close,
                "volume": float(v or 0),
            }
        )

    order = [_parse_timestamp(b["timestamp"]) for b in bars]
    inversions = sum(1 for a, b in zip(order, order[1:]) if b < a)
    if inversions:
        _ERR(
            f"WARNING: 输入数据有 {inversions} 处时间倒序，按时间升序重排"
            f"（重排前后 bar 数不变: {len(bars)}）\n"
        )
        bars.sort(key=lambda b: _parse_timestamp(b["timestamp"]))
    return bars


def apply_limit(bars: list[dict], limit: int | None) -> list[dict]:
    """``--limit`` 只保留**最后** N 根（回测最关心近期行情，且能压住耗时）。"""
    if limit is None or limit <= 0:
        return bars
    if limit >= len(bars):
        return bars
    return bars[-limit:]


# ──────────────────────────────────────────────────────────────────────────────
# 策略装载
# ──────────────────────────────────────────────────────────────────────────────


def _install_strategies_package_shim() -> None:
    """让 ``from .portfolio import ...`` 在 backend/strategies/ 下可用（见模块 docstring）。

    只在该目录**没有**真实 ``portfolio.py`` 时装别名，避免盖掉将来真正的实现。
    """
    if str(STRATEGIES_DIR) not in sys.path:
        sys.path.insert(0, str(STRATEGIES_DIR))
    if "strategies" not in sys.modules:
        importlib.import_module("strategies")

    if (STRATEGIES_DIR / "portfolio.py").is_file():
        return
    alias = sys.modules.get("strategies.portfolio")
    if alias is None:
        from apps.strategy_engine import portfolio as engine_portfolio

        sys.modules["strategies.portfolio"] = engine_portfolio
        package = sys.modules.get("strategies")
        if package is not None:
            setattr(package, "portfolio", engine_portfolio)


def resolve_strategy_file(strategy_file: str) -> Path:
    """``--strategy-file`` 可以是绝对路径、相对 CWD，或相对 backend/strategies/。"""
    candidate = Path(strategy_file)
    for path in (
        candidate,
        STRATEGIES_DIR / candidate.name,
        BACKEND_DIR / candidate,
        Path.home() / ".tradelogx" / "strategies" / candidate.name,
    ):
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        f"找不到策略文件 {strategy_file!r}（已尝试 CWD、{STRATEGIES_DIR}、"
        f"{BACKEND_DIR}、~/.tradelogx/strategies）"
    )


def load_strategy_module(file_path: Path, strategy_name: str | None):
    """按文件加载策略模块并返回 ``(模块, 模块名)``；注册是 import 的副作用。"""
    if file_path.parent == STRATEGIES_DIR.resolve():
        _install_strategies_package_shim()
        module_name = f"strategies.{file_path.stem}"
    else:
        module_name = file_path.stem

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法从 {file_path} 构造 import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    logger.debug("loaded strategy module %s from %s", module_name, file_path)
    return module, module_name


def pick_strategy_class(module, strategy_name: str | None):
    """确定要跑的策略类。

    优先用 ``--strategy-name`` 查注册表（这是 ``StrategyRunner``/实盘同一条取类
    路径）；查不到再退回扫描本模块里的 BaseStrategy 子类——``rsi_cross.py``
    这类文件根本没有 ``@register_strategy`` 装饰器，注册表里查不到它。
    """
    from apps.strategy_engine.loader import _find_strategy_class
    from apps.strategy_engine.registry import StrategyRegistry

    if strategy_name:
        try:
            return StrategyRegistry.get_class(strategy_name), strategy_name
        except ValueError:
            _ERR(
                f"WARNING: 注册表里没有 {strategy_name!r}"
                f"（已注册: {len(StrategyRegistry.list_registered())} 个），"
                "改为扫描策略文件内的类\n"
            )

    cls = _find_strategy_class(module)
    if strategy_name and cls.name != strategy_name:
        _ERR(
            f"WARNING: --strategy-name={strategy_name!r} 与文件内策略类名 "
            f"{cls.name!r} 不一致，按文件内的类跑\n"
        )
    return cls, cls.name


# ──────────────────────────────────────────────────────────────────────────────
# 统计派生
# ──────────────────────────────────────────────────────────────────────────────


def _fmt_ratio(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, str):
        return value
    return f"{value:.{digits}f}"


def _fmt_money(value, digits: int = 2) -> str:
    return f"{value:,.{digits}f}"


def _trade_sort_key(trade: dict) -> tuple:
    """同 pnl 时的确定性次序：时间 → 出场时间 → 方向。"""
    return (
        str(trade.get("entry_time") or ""),
        str(trade.get("exit_time") or ""),
        str(trade.get("side") or ""),
    )


def derive_stats(
    stats: dict, timeframe: str, initial_capital: float | None = None
) -> dict:
    """引擎统计 ⊕ 本脚本需要的派生口径（多空拆分 / 出场原因 / 持仓时长 / 成本）。"""
    from apps.datasource.exchange import timeframe_to_seconds

    trades = list(stats.get("trades") or [])
    closed = [t for t in trades if t.get("pnl") is not None]
    # ── 账本口径：一笔「开仓腿」+ 一笔「平仓腿」= 一条完整交易 ──
    #
    # 引擎把开仓和平仓都写成 trade 记录：开仓 `trade_type=open/add, pnl=None`，
    # 平仓 `trade_type=close, pnl=<数值>`。**只有 close 行算「一笔交易」**
    # （引擎的 total_trades / win_rate 也是这个口径）。所以对一笔一开一平的
    # 策略，开仓腿数 == 已平仓数，两者相等是**正确**的，不是重复计数。
    #
    # 「结束时是否还有裸露持仓」无法从这些行数直接看出（开仓腿多一条也可能是
    # 加仓），只能用净额账本核：多头开 +qty / 空头开 -qty / 多头平 -qty /
    # 空头平 +qty，求和应为 0（引擎在最后一根强制平仓）。非 0 说明
    # forced-close 没生效——那才是要报警的东西。
    entry_legs = [t for t in trades if t.get("trade_type") != "close"]
    exit_legs = [t for t in trades if t.get("trade_type") == "close"]
    residual_qty = 0.0
    for trade in trades:
        qty = float(trade.get("quantity") or 0.0)
        side = trade.get("side")
        if trade.get("trade_type") == "close":
            residual_qty += -qty if side == "long" else qty
        else:
            residual_qty += qty if side == "long" else -qty

    bar_seconds = float(timeframe_to_seconds(timeframe))

    long_trades = [t for t in closed if t.get("side") == "long"]
    short_trades = [t for t in closed if t.get("side") == "short"]

    def _win_rate(items: list[dict]) -> float | None:
        if not items:
            return None
        wins = sum(1 for t in items if (t.get("pnl") or 0) > 0)
        return wins / len(items)

    exit_reasons: dict[str, int] = {}
    for trade in closed:
        reason = str(trade.get("exit_reason") or "") or "(empty)"
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    held_bars: list[float] = []
    skipped_held = 0
    for trade in closed:
        try:
            entry = _parse_timestamp(trade["entry_time"])
            exit_at = _parse_timestamp(trade["exit_time"])
        except (KeyError, ValueError, TypeError):
            skipped_held += 1
            continue
        held_bars.append((exit_at - entry).total_seconds() / bar_seconds)

    pnl_pcts = [float(t["pnl_pct"]) for t in closed if t.get("pnl_pct") is not None]

    cost_detail = dict(stats.get("cost_detail") or {})
    commission = float(cost_detail.get("commission") or 0.0)
    slippage = float(cost_detail.get("slippage") or 0.0)
    funding = float(cost_detail.get("funding") or 0.0)

    # ── 账本对账：为什么 Σ(已平仓 pnl) ≠ 期末权益 - 初始资金 ──
    #
    # 引擎的 ``pnl`` 只在**平仓腿**上记，公式是
    # ``(exit_price - avg_entry) * qty - 平仓腿佣金``，因此它已经含了：
    #   * 滑点（开仓价、平仓价里都已带滑点劣化）
    #   * 平仓腿佣金
    # **没含**开仓腿佣金（那笔在开仓时就已从现金扣掉，见 _apply_buy_fill）。
    #
    # 恒等式因此是（实测 gap 恰为 0.0000）：
    #
    #     equity_change == Σ(平仓 pnl) - Σ(开仓腿佣金)
    #
    # 关键：**不能**再减开仓腿滑点。滑点不是独立的现金扣减项，它已经烙在
    # 成交价里，减两次就是重复计入——本脚本第一版正是这么错的，gap 立刻变成
    # +1,612.95，恰好等于开仓腿滑点。``cost_detail.slippage`` 是**归因口径**
    # （「这些滑点值多少钱」），不是现金流。
    closed_pnl_sum = sum(float(t.get("pnl") or 0.0) for t in closed)
    entry_leg_commission = sum(float(t.get("commission") or 0.0) for t in entry_legs)
    entry_leg_slippage = sum(float(t.get("slippage") or 0.0) for t in entry_legs)
    exit_leg_commission = sum(float(t.get("commission") or 0.0) for t in closed)
    exit_leg_slippage = sum(float(t.get("slippage") or 0.0) for t in closed)

    equity_change = None
    gross_pnl_estimate = None
    if initial_capital is not None:
        equity_change = float(stats.get("final_equity") or 0.0) - float(initial_capital)
        # 零成本毛收益的**线性估计**：把已付的手续费与滑点全部加回净变化。
        # 这是「成本加回、路径不变」的近似，**不**等于 commission=0 重跑
        # （零成本会改变权益路径，进而影响后续每笔的仓位大小）。
        # 实测 295,028 根 BTC 5m 默认参数：本估计 +12.95%，真零成本重跑 +10.37%。
        # 用它判断「这套参数是不是本来就不赚钱」，不要当精确值引用。
        gross_pnl_estimate = equity_change + commission + slippage + funding

    return {
        "closed_trades": len(closed),
        "open_legs": len(entry_legs),
        "close_legs": len(exit_legs),
        "residual_qty": residual_qty,
        "closed_pnl_sum": closed_pnl_sum,
        "equity_change": equity_change,
        "gross_pnl_estimate": gross_pnl_estimate,
        "entry_leg_commission": entry_leg_commission,
        "entry_leg_slippage": entry_leg_slippage,
        "exit_leg_commission": exit_leg_commission,
        "exit_leg_slippage": exit_leg_slippage,
        "long_trades": len(long_trades),
        "long_win_rate": _win_rate(long_trades),
        "short_trades": len(short_trades),
        "short_win_rate": _win_rate(short_trades),
        "exit_reason_counts": dict(sorted(exit_reasons.items())),
        "avg_bars_held": (sum(held_bars) / len(held_bars)) if held_bars else None,
        "avg_bars_held_trades": len(held_bars),
        "avg_bars_held_skipped": skipped_held,
        "max_bars_held": max(held_bars) if held_bars else None,
        "avg_pnl_pct": (sum(pnl_pcts) / len(pnl_pcts)) if pnl_pcts else None,
        "total_commission": commission,
        "total_slippage": slippage,
        "total_funding": funding,
        "total_costs": commission + slippage + funding,
        "timeframe_seconds": bar_seconds,
    }


def _public_stats(stats: dict, derived: dict) -> dict:
    """落盘的 stats：去掉 trades/equity_curve（它们单独成字段，避免重复两遍）。"""
    blob = {
        key: value
        for key, value in stats.items()
        if key not in ("trades", "equity_curve")
    }
    blob.update(derived)
    return blob


def _compact_equity_curve(points: list[dict]) -> list[dict]:
    """权益曲线原样保留、只压小数位（每根 K 线一个点，20 万根 ~10MB）。"""
    return [
        {
            "timestamp": point.get("timestamp", ""),
            "equity": round(float(point.get("equity", 0.0)), 2),
            "drawdown": round(float(point.get("drawdown", 0.0)), 6),
        }
        for point in points
    ]


# ──────────────────────────────────────────────────────────────────────────────
# 打印
# ──────────────────────────────────────────────────────────────────────────────


def _rule(char: str = "-", width: int = 78) -> None:
    _OUT(char * width + "\n")


def format_trade_line(rank: int, trade: dict) -> str:
    pnl = float(trade.get("pnl") or 0.0)
    pnl_pct = trade.get("pnl_pct")
    pnl_pct_text = f"{float(pnl_pct) * 100:+.4f}%" if pnl_pct is not None else "n/a"
    exit_price = trade.get("exit_price")
    return (
        f"  {rank}. {trade.get('entry_time') or '?'} -> {trade.get('exit_time') or '?'}  "
        f"{str(trade.get('side') or '?'):<5} qty={float(trade.get('quantity') or 0):.6f}  "
        f"entry={float(trade.get('entry_price') or 0):.4f} "
        f"exit={'n/a' if exit_price is None else f'{float(exit_price):.4f}'}  "
        f"pnl={pnl:+.4f} ({pnl_pct_text})  reason={trade.get('exit_reason') or '(empty)'}"
    )


def print_run_report(
    index: int,
    total_runs: int,
    cls,
    file_path: Path,
    params: dict,
    args,
    bars: list[dict],
    stats: dict,
    derived: dict,
) -> None:
    _OUT("\n")
    _rule("=")
    _OUT(f"RUN {index}/{total_runs}   params={_json_inline(params)}\n")
    _rule("=")
    _OUT(f"strategy          : {cls.name}  ({cls.__name__})\n")
    _OUT(f"source            : {file_path}\n")
    _OUT(
        f"market            : {args.symbol} {args.timeframe}  "
        f"bars={len(bars)}  {bars[0]['timestamp']} -> {bars[-1]['timestamp']}\n"
    )
    _OUT(
        f"costs             : commission={args.commission}/side "
        f"slippage={args.slippage}/side short_margin_rate={args.short_margin_rate} "
        f"is_perp={args.is_perp}\n"
    )
    _OUT(
        f"capital           : {args.capital:,.2f} -> final_equity "
        f"{_fmt_money(float(stats.get('final_equity') or 0.0))}\n"
    )
    _rule()
    _OUT(f"total_return_pct  : {_fmt_ratio(stats.get('total_return_pct'), 4)}\n")
    _OUT(
        f"max_drawdown_pct  : {_fmt_ratio(stats.get('max_drawdown_pct'), 4)}"
        "   (negative = drawdown from peak)\n"
    )
    _OUT(f"win_rate          : {_pct(stats.get('win_rate'))}\n")
    _OUT(f"total_trades      : {stats.get('total_trades')}\n")
    _OUT(f"profit_factor     : {_fmt_ratio(stats.get('profit_factor'), 4)}\n")
    _OUT(f"payoff_ratio      : {_fmt_ratio(stats.get('payoff_ratio'), 4)}\n")
    _OUT(f"sharpe_ratio      : {_fmt_ratio(stats.get('sharpe_ratio'), 4)}\n")
    _rule()
    _OUT(
        f"long_trades       : {derived['long_trades']}  "
        f"long_win_rate={_pct(derived['long_win_rate'])}\n"
    )
    _OUT(
        f"short_trades      : {derived['short_trades']}  "
        f"short_win_rate={_pct(derived['short_win_rate'])}\n"
    )
    # 开仓腿与平仓腿相等是正常口径（一开一平 = 一条交易），所以这里只报
    # 「账本是否平」——不平才是异常。行数本身不构成告警。
    _OUT(
        f"trade legs        : open_legs={derived['open_legs']} "
        f"close_legs={derived['close_legs']} "
        f"residual_qty={derived['residual_qty']:.6f}"
        + (
            "  <-- 结束时仍有净持仓（forced close 未生效）\n"
            if abs(derived["residual_qty"]) > 1e-9
            else "  (net flat at end, as expected)\n"
        )
    )
    _OUT(f"exit_reasons      : {_json_inline(derived['exit_reason_counts'])}\n")
    _OUT(
        f"avg_bars_held     : {_fmt_ratio(derived['avg_bars_held'], 2)}"
        f"   (max {_fmt_ratio(derived['max_bars_held'], 2)}, "
        f"from {derived['avg_bars_held_trades']} closed trades"
        + (
            f", {derived['avg_bars_held_skipped']} skipped for bad timestamps"
            if derived["avg_bars_held_skipped"]
            else ""
        )
        + ")\n"
    )
    _OUT(f"avg_pnl_pct       : {_pct(derived['avg_pnl_pct'], signed=True)}\n")
    _OUT(
        f"costs paid        : commission={derived['total_commission']:,.4f} "
        f"slippage={derived['total_slippage']:,.4f} "
        f"funding={derived['total_funding']:,.4f} "
        f"total={derived['total_costs']:,.4f}\n"
    )
    # 账本对账：Σ(平仓 pnl) 与期末权益变化之间应当**正好**差「开仓腿佣金」。
    # 不打印这一截的话，-99.96% 的净收益和 -5,963.88 的 pnl 之和看起来像
    # 引擎算错了，其实只是两处口径不同（见 derive_stats 里的长注释）。
    if derived["equity_change"] is not None:
        reconciled = derived["closed_pnl_sum"] - derived["entry_leg_commission"]
        gap = derived["equity_change"] - reconciled
        _OUT(
            f"ledger            : sum(closed pnl)={derived['closed_pnl_sum']:,.4f} "
            f"- entry_leg_commission={derived['entry_leg_commission']:,.4f} "
            f"-> {reconciled:,.4f}  vs equity_change={derived['equity_change']:,.4f} "
            f"(unexplained gap={gap:+,.4f})\n"
        )
        _OUT(
            f"zero-cost est.    : {derived['gross_pnl_estimate']:,.4f} "
            f"({derived['gross_pnl_estimate'] / float(args.capital) * 100:+.4f}% of capital) "
            "= 净变化 + 已付成本（线性近似，非零成本重跑）\n"
        )
        if abs(gap) > max(1e-4, abs(derived["equity_change"]) * 1e-6):
            _OUT(
                "                     ^ NOTE: gap 非 0 → 还有未归因的现金变动"
                "（资金费率 / 部分成交 / 未平仓市值），不要当成 0\n"
            )

    closed = [t for t in stats.get("trades") or [] if t.get("pnl") is not None]
    if not closed:
        _OUT("\n!!! WARNING: 本组参数产出 0 笔已平仓交易 —— 不是「策略无效」，是\n")
        _OUT("!!!          「这次回测没告诉任何事」。检查：数据是否够 warmup、\n")
        _OUT("!!!          参数是否让入场条件永不成立、symbol/timeframe 是否对。\n")
        return

    ranked = sorted(
        closed, key=lambda t: (float(t.get("pnl") or 0.0), _trade_sort_key(t))
    )
    best = list(reversed(ranked[-TOP_N_TRADES:]))
    worst = ranked[:TOP_N_TRADES]

    _rule()
    _OUT(f"top {len(best)} best trades (by pnl)\n")
    for rank, trade in enumerate(best, start=1):
        _OUT(format_trade_line(rank, trade) + "\n")
    _OUT(f"\ntop {len(worst)} worst trades (by pnl)\n")
    for rank, trade in enumerate(worst, start=1):
        _OUT(format_trade_line(rank, trade) + "\n")


def _pct(value, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    sign = "+" if signed else ""
    return f"{float(value) * 100:{sign}.{digits}f}%"


def _json_inline(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


# ──────────────────────────────────────────────────────────────────────────────
# warmup 自检
# ──────────────────────────────────────────────────────────────────────────────


def warmup_check(cls, params: dict, bars: list[dict], args) -> dict:
    """只用 warmup 长度的数据，验证「策略能实例化 + 预热后能否产出洞察」。

    不跑撮合（引擎需要满负荷历史）；这里直接走 ``generate_insights`` 这条
    5 步管线里的第 2 步，数它吐了几个 insight、第一个出现在第几根之后。
    这回答的是「warmup 够不够、参数能不能被 __init__ 吃下去」，
    而不是「策略赚不赚钱」——后者必须有撮合，不要用这个模式下结论。
    """
    from apps.strategy_engine.base import StrategyContext

    min_bars = int(getattr(cls, "min_kline_length", 0) or 0)
    warm_bars = min(min_bars, len(bars)) if min_bars else min(64, len(bars))
    if warm_bars <= 0:
        warm_bars = len(bars)

    ctx = StrategyContext(
        symbol=args.symbol,
        timeframe=args.timeframe,
        mode="backtest",
        params=dict(params),
        balance=Decimal(str(args.capital)),
    )
    strategy = cls(ctx)
    strategy.on_start()

    signal_counts: dict[str, int] = {}
    first_signal_bar: int | None = None
    empty_insight_bars = 0
    # 同样走 HistoryView，避免 warmup 自检也踩 O(n²)（warm_bars 可达 500+）。
    # 这里多一层兜底：要求真 list 的策略会抛 ValueError，warmup 只是自检，
    # 退成真 list 完全可接受（warm_bars 很小），没必要为此中断。
    use_view = True
    view = HistoryView(bars)
    for i in range(warm_bars):
        kline = bars[i]
        ctx.set_price(args.symbol, Decimal(str(kline["close"])))
        history = view[: i + 1] if use_view else bars[: i + 1]
        try:
            insights = strategy.generate_insights(kline, history)
        except (ValueError, TypeError) as exc:
            if not use_view or not any(
                marker in str(exc) for marker in _LIST_SHAPE_ERRORS
            ):
                raise
            _ERR(
                f"WARNING: warmup 自检里 {cls.name} 同样要求真 list，退回真 list 重来\n"
            )
            use_view = False
            strategy = cls(ctx)
            strategy.on_start()
            signal_counts, first_signal_bar, empty_insight_bars = {}, None, 0
            insights = strategy.generate_insights(kline, bars[: i + 1])
        if not insights:
            empty_insight_bars += 1
            continue
        if first_signal_bar is None:
            first_signal_bar = i
        for insight in insights:
            key = insight.signal_name or "(unnamed)"
            signal_counts[key] = signal_counts.get(key, 0) + 1
    strategy.on_stop()

    return {
        "min_kline_length": min_bars,
        "bars_available": len(bars),
        "bars_used": warm_bars,
        "warmup_satisfied": min_bars == 0 or len(bars) >= min_bars,
        "signals_by_name": dict(sorted(signal_counts.items())),
        "total_signals": sum(signal_counts.values()),
        "first_signal_bar_index": first_signal_bar,
        "bars_without_insight": empty_insight_bars,
    }


def print_warmup_report(
    index: int, total_runs: int, cls, file_path: Path, params: dict, report: dict
) -> None:
    _OUT("\n")
    _rule("=")
    _OUT(f"WARMUP CHECK {index}/{total_runs}   params={_json_inline(params)}\n")
    _rule("=")
    _OUT(f"strategy          : {cls.name}  ({cls.__name__})\n")
    _OUT(f"source            : {file_path}\n")
    _OUT(
        f"schema keys       : {len(getattr(cls, 'params_schema', {}) or {})}  "
        f"supports_short={bool(getattr(cls, 'supports_short', False))}\n"
    )
    _OUT(
        f"min_kline_length  : {report['min_kline_length']}  "
        f"bars_available={report['bars_available']}  "
        f"warmup_satisfied={report['warmup_satisfied']}\n"
    )
    _OUT(f"bars_used         : {report['bars_used']} (insight-only, no fills)\n")
    _OUT(f"total_signals     : {report['total_signals']}\n")
    _OUT(f"first_signal_bar  : {report['first_signal_bar_index']}\n")
    _OUT(f"signals_by_name   : {_json_inline(report['signals_by_name'])}\n")
    if report["total_signals"] == 0:
        _OUT(
            "\nWARNING: warmup 区间内策略一个洞察都没产出。warmup 不够，"
            "或参数把入场条件关死了。\n"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 运行
# ──────────────────────────────────────────────────────────────────────────────


def run_engine(cls, params: dict, bars: list[dict], args, *, use_view: bool = True):
    """搭上下文 + 引擎并跑完；返回 ``(strategy, engine, stats)``。不写 DB。

    ``ohlcv_data`` 默认传 ``HistoryView``，不是 ``bars`` 本身 —— 见
    ``HistoryView`` 的 docstring：引擎逐 bar 切前缀，list 会退化成 O(n²)。

    ``use_view=False`` 是兼容退路（见 ``run_engine_with_fallback``）：有些策略
    会把引擎传下来的 ``history`` 再往下传给**要求真 list** 的指标函数，例如
    ``detect_box_range`` 开头就 ``if not isinstance(klines, list): raise``。
    视图不是 list，那类策略会直接抛 ``ValueError``。此时退回真 list 能跑，
    但代价是 O(n²)——小数据量下无所谓，全量数据请改用不挑类型的策略。
    """
    from apps.strategy_engine.backtest_mode import BacktestEngine
    from apps.strategy_engine.base import StrategyContext

    capital = Decimal(str(args.capital))
    ctx = StrategyContext(
        symbol=args.symbol,
        timeframe=args.timeframe,
        mode="backtest",
        params=dict(params),
        balance=capital,
        position=Decimal("0"),
    )
    strategy = cls(ctx)
    engine = BacktestEngine(
        strategy=strategy,
        ohlcv_data=HistoryView(bars) if use_view else list(bars),
        initial_capital=capital,
        symbol=args.symbol,
        timeframe=args.timeframe,
        commission_rate=Decimal(str(args.commission)),
        slippage_rate=Decimal(str(args.slippage)),
        is_perp=bool(args.is_perp),
        funding_rates={},
        short_margin_rate=Decimal(str(args.short_margin_rate)),
    )
    stats = asyncio.run(engine.run())
    return strategy, engine, stats


# 「策略要求真 list」的失败特征。不做宽泛的 ``except Exception`` 重试：
# 那只会在真 bug 上把错误重放一遍、并把它伪装成「只是慢一点」。
_LIST_SHAPE_ERRORS = (
    "klines 必须是 K线 dict 列表",
    "必须是 K线 dict 列表",
    "must be a list",
)


def run_engine_with_fallback(cls, params: dict, bars: list[dict], args):
    """先零拷贝跑；若策略因「history 不是 list」失败，则如实退回并告警。"""
    try:
        return run_engine(cls, params, bars, args, use_view=True), True
    except (ValueError, TypeError) as exc:
        message = str(exc)
        if not any(marker in message for marker in _LIST_SHAPE_ERRORS):
            raise
        _ERR(
            f"WARNING: 策略 {cls.name} 的指标函数要求 history 是真正的 list，"
            f"零拷贝视图被拒（{message}）——\n"
            "WARNING: 退回真 list 重跑，本次耗时 O(n²)。全量数据建议换用不挑类型的策略。\n"
        )
        return run_engine(cls, params, bars, args, use_view=False), False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_unknown_params(cls, params: dict) -> None:
    schema = getattr(cls, "params_schema", None) or {}
    unknown = sorted(set(params) - set(schema))
    if unknown:
        _ERR(
            f"WARNING: 参数 {unknown} 不在 {cls.name} 的 params_schema 里 —— "
            "策略可能完全不读它们（拼写错会静默变成「没生效」）\n"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="离线回测 harness：复用 apps.strategy_engine.backtest_mode.BacktestEngine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data", required=True, help="OHLCV JSON 路径（必填）")
    parser.add_argument(
        "--strategy-file",
        default=DEFAULT_STRATEGY_FILE,
        help="策略文件名（相对 backend/strategies/）或绝对路径",
    )
    parser.add_argument(
        "--strategy-name",
        default=None,
        help="注册表里的策略名；省略则用文件内扫到的策略类",
    )
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0002, help="单边滑点率")
    parser.add_argument("--short-margin-rate", type=float, default=0.2)
    parser.add_argument("--is-perp", action="store_true", help="按永续合约结算资金费率")
    parser.add_argument(
        "--params", default="", help='e.g. "rr_ratio=1.5,max_hold_bars=48"'
    )
    parser.add_argument(
        "--sweep", default=None, help="JSON 文件：[{param: value}, ...]，逐组跑"
    )
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径")
    parser.add_argument("--limit", type=int, default=None, help="只跑最后 N 根 K 线")
    parser.add_argument(
        "--warmup-only",
        action="store_true",
        help="只做 warmup/参数自检（不撮合、不要求有交易）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        base_params = parse_kv_params(args.params)
        sweep_params = load_sweep(args.sweep) if args.sweep else None
    except (ValueError, OSError) as exc:
        _ERR(f"ERROR: 参数解析失败: {exc}\n")
        return 2

    if sweep_params is not None:
        run_params = [{**base_params, **override} for override in sweep_params]
    else:
        run_params = [base_params]

    if not run_params:
        _ERR("ERROR: --sweep 是空数组，没有任何参数组可跑\n")
        return 2

    bootstrap_django()

    from apps.strategy_engine.runner import resolve_params

    try:
        file_path = resolve_strategy_file(args.strategy_file)
        module, module_name = load_strategy_module(file_path, args.strategy_name)
        cls, resolved_name = pick_strategy_class(module, args.strategy_name)
    except (OSError, ValueError, ImportError) as exc:
        _ERR(f"ERROR: 策略加载失败: {type(exc).__name__}: {exc}\n")
        return 2

    try:
        bars_all = load_bars(args.data)
    except (OSError, ValueError) as exc:
        _ERR(f"ERROR: 数据加载失败: {type(exc).__name__}: {exc}\n")
        return 2

    bars = apply_limit(bars_all, args.limit)
    if not bars:
        _ERR(f"ERROR: {args.data} 里没有可用 K 线（--limit={args.limit}）\n")
        return 2

    _OUT(f"harness           : {SCRIPT_REL_PATH}\n")
    if len(bars) != len(bars_all):
        _OUT(
            f"data              : {Path(args.data).resolve()}  "
            f"({len(bars_all)} bars, using last {len(bars)} via --limit)\n"
        )
    else:
        _OUT(
            f"data              : {Path(args.data).resolve()}  ({len(bars_all)} bars)\n"
        )
    _OUT(f"strategy          : {resolved_name}  <- {module_name}  ({file_path})\n")
    if args.is_perp:
        _ERR(
            "WARNING: --is-perp=True 但没有资金费率数据（CLI 无该输入），"
            "引擎会在每个结算点告警并**跳过**结算 —— 这些数字不含资金费率成本\n"
        )

    # ── warmup-only 分支：只看预热与参数，不撮合 ──
    if args.warmup_only:
        reports = []
        for index, override in enumerate(run_params, start=1):
            params = resolve_params(cls, override)
            _check_unknown_params(cls, params)
            report = warmup_check(cls, params, bars, args)
            print_warmup_report(index, len(run_params), cls, file_path, params, report)
            reports.append({"params": params, "warmup": report})
        if args.out:
            payload = {
                "meta": _build_meta(args, file_path, module_name, resolved_name, bars),
                "mode": "warmup_only",
                "runs": reports,
            }
            if not _write_out(args.out, payload):
                return 2
        _OUT("\nwarmup-only: 未跑撮合，退出码 0（不是回测结论）\n")
        return 0

    # ── 正常回测 ──
    runs_out: list[dict] = []
    zero_trade_runs: list[int] = []
    for index, override in enumerate(run_params, start=1):
        params = resolve_params(cls, override)
        _check_unknown_params(cls, params)
        try:
            (_strategy, _engine, stats), used_view = run_engine_with_fallback(
                cls, params, bars, args
            )
        except Exception as exc:  # noqa: BLE001 - CLI 边界：给一行可读错误，不吞栈
            _ERR(f"ERROR: 第 {index} 组参数运行失败: {type(exc).__name__}: {exc}\n")
            return 2
        derived = derive_stats(stats, args.timeframe, initial_capital=args.capital)
        print_run_report(
            index, len(run_params), cls, file_path, params, args, bars, stats, derived
        )
        if derived["closed_trades"] == 0:
            zero_trade_runs.append(index)
        runs_out.append(
            {
                "params": params,
                "stats": _public_stats(stats, derived),
                "zero_copy_history": used_view,
                "trades": stats.get("trades") or [],
                "equity_curve": _compact_equity_curve(stats.get("equity_curve") or []),
            }
        )

    if args.out:
        payload = {
            "meta": _build_meta(args, file_path, module_name, resolved_name, bars),
            "runs": runs_out,
        }
        if not _write_out(args.out, payload):
            return 2

    if zero_trade_runs:
        # 先冲掉 stdout：stdout 是块缓冲、stderr 无缓冲，混管道时 FAILURE 块
        # 会跑到前面去，看起来像「还没跑就失败了」。
        sys.stdout.flush()
        _ERR("\n")
        _ERR("!" * 78 + "\n")
        _ERR(
            f"!!! FAILURE: {len(zero_trade_runs)}/{len(run_params)} 组参数产出 0 笔"
            f"已平仓交易（run {zero_trade_runs}）\n"
        )
        _ERR("!!! 这不是「策略没赚到钱」，是「这次回测什么也没测到」。退出码 1。\n")
        _ERR("!" * 78 + "\n")
        return 1

    _OUT(f"\nOK: {len(run_params)} 组参数均产出已平仓交易\n")
    return 0


def _build_meta(args, file_path: Path, module_name: str, strategy_name: str, bars):
    """``--out`` 的 meta：只写确定性字段（不含 wall-clock、不含本机随机信息）。"""
    from apps.datasource.exchange import timeframe_to_seconds
    from apps.strategy_engine.backtest_mode import bars_per_year

    try:
        strategy_sha = _file_sha256(file_path)
    except OSError:
        strategy_sha = ""
    try:
        data_sha = _file_sha256(Path(args.data).resolve())
    except OSError:
        data_sha = ""

    return {
        "harness": SCRIPT_REL_PATH,
        "engine": "apps.strategy_engine.backtest_mode.BacktestEngine",
        "strategy_file": str(file_path),
        "strategy_module": module_name,
        "strategy_name": strategy_name,
        "strategy_sha256": strategy_sha,
        "data": {
            "path": str(Path(args.data).resolve()),
            "sha256": data_sha,
            "bars_used": len(bars),
            "first_bar": bars[0]["timestamp"] if bars else "",
            "last_bar": bars[-1]["timestamp"] if bars else "",
            "limit": args.limit,
        },
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "timeframe_seconds": timeframe_to_seconds(args.timeframe),
        "bars_per_year": round(bars_per_year(args.timeframe), 6),
        "initial_capital": float(args.capital),
        "commission_rate": float(args.commission),
        "slippage_rate": float(args.slippage),
        "short_margin_rate": float(args.short_margin_rate),
        "is_perp": bool(args.is_perp),
        "funding_rates_supplied": False,
        "notes": (
            [
                "is_perp=True but no funding rate data was supplied; the engine "
                "skips every settlement (warns) — results exclude funding cost",
            ]
            if args.is_perp
            else []
        ),
        "stats_definitions": {
            "profit_factor": "sum(wins)/abs(sum(losses)) on closed trades; null when no losses",
            "payoff_ratio": "avg(win)/avg(loss) on closed trades; null when either side empty",
            "sharpe_ratio": "mean/std of per-bar equity returns * sqrt(bars_per_year)",
            "max_drawdown_pct": "negative percentage from peak equity",
            "avg_bars_held": "mean((exit_time-entry_time)/timeframe_seconds) over closed trades",
        },
    }


def _write_out(path: str, payload: dict) -> bool:
    target = Path(path)
    try:
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, ensure_ascii=False, default=str)
            handle.write("\n")
    except OSError as exc:
        _ERR(f"ERROR: 写 {path} 失败: {exc}\n")
        return False
    size_mb = target.stat().st_size / (1024 * 1024)
    _OUT(
        f"\nwrote {target} ({size_mb:.2f} MB, {len(payload.get('runs') or [])} runs)\n"
    )
    return True


if __name__ == "__main__":
    sys.exit(main())
