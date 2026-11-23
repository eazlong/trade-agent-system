"""二维市场状态：**独立验证脚本**（不改生产代码、不写库）。

目的：在动核心代码之前，先用真实历史数据回答「新结构能带来多少改善」。
它把 quant.py 的判定逻辑重写为二维版本，与现有 label_series 逐日对比。

## 为什么是独立脚本而不是改 quant.py

第 1 步的产出是一张**对比表**，不是新机制。把判定逻辑搬进 quant.py 会让「旧 vs 新」
的对照变成两份不同时点的代码，而那种对照回答不了「改了之后会怎样」。脚本里两条逻辑
并排调用同一批日线，差异只来自判定规则本身。

## 两个维度

- **周期 cycle**：bull / bear / sideways / unclear（慢变量，方向）
- **波动 volatility**：normal / event_spike（快变量，叠加层，用价格代理）

**不互斥**：bull + event_spike 是合法状态（牛市里的暴跌日）。
这正是现有四档结构做不到的事——HIGH_VOL 排第一，波动一高周期信息就被丢弃。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app")
os.chdir("/app")

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.prod")
django.setup()

from collections import Counter  # noqa: E402

import numpy as np  # noqa: E402

from apps.regime import config, truth  # noqa: E402
from apps.regime.models import DailyCandle, RegimeTruthInterval as T  # noqa: E402
from apps.regime.quant import BaseRegime, label_series  # noqa: E402
from apps.strategy_engine.indicators import atr as _atr  # noqa: E402
from apps.strategy_engine.indicators import ema as _ema  # noqa: E402

SYMBOL = config.CANDLES.symbol


# --------------------------------------------------------------------------- #
# 取数
# --------------------------------------------------------------------------- #


def load() -> list[dict]:
    return list(
        DailyCandle.objects.filter(symbol=SYMBOL)
        .order_by("date")
        .values("date", "open", "high", "low", "close", "volume")
    )


def arrays(rows):
    d = [r["date"] for r in rows]
    o = np.array([float(r["open"]) for r in rows], dtype=np.float64)
    h = np.array([float(r["high"]) for r in rows], dtype=np.float64)
    lo = np.array([float(r["low"]) for r in rows], dtype=np.float64)
    c = np.array([float(r["close"]) for r in rows], dtype=np.float64)
    v = np.array([float(r["volume"]) for r in rows], dtype=np.float64)
    return d, o, h, lo, c, v


# --------------------------------------------------------------------------- #
# 新判定：两个维度
# --------------------------------------------------------------------------- #

#: 周期维参数（**先取一组保守的起手值**，本步只为量出改善空间，不是定稿）
CYCLE = {
    "ema_fast": 20,
    "ema_slow": 60,
    "atr_period": 14,
    "slope_lookback": 5,
    "sep_min_atr": 0.35,  # 显式横盘的门槛（比旧 0.5 松，但**不再是唯一路径**）
    "strength_min": 0.15,  # |slope| / ATR 的强度下限
    "consistency_window": 20,  # 一致性回看窗口
    "consistency_min": 0.60,  # 窗口内同向占比
}

#: 波动维参数（价格代理）
VOL = {
    "gap_ratio": 0.02,  # 跳空 > 2%
    "volume_multiple": 2.0,  # 放量 > 2 倍 20 日均量
    "atr_jump_ratio": 1.5,  # ATR 相对前一日 > 1.5 倍
    "move_ratio": 0.05,  # 单日涨跌 > 5%
}


def classify(rows):
    """逐日产出 (cycle, volatility, 依据数字)。"""
    d, o, h, lo, c, v = arrays(rows)
    n = len(c)
    p = CYCLE

    atr = _atr(h, lo, c, period=p["atr_period"])
    ema_f = _ema(c, p["ema_fast"])
    ema_s = _ema(c, p["ema_slow"])

    # 均量：20 日
    vol_ma = np.full(n, np.nan)
    w = 20
    for i in range(n):
        if i + 1 >= w:
            vol_ma[i] = v[i - w + 1 : i + 1].mean()

    out = []
    for i in range(n):
        cycle = "unclear"
        volatility = "normal"
        why = {}

        # ---- 周期维 ----
        slope = None
        si = i - p["slope_lookback"]
        if si >= 0 and np.isfinite(ema_f[i]) and np.isfinite(ema_f[si]):
            slope = float(ema_f[i] - ema_f[si])

        sep = None
        if (
            np.isfinite(atr[i])
            and atr[i] > 0
            and np.isfinite(ema_f[i])
            and np.isfinite(ema_s[i])
        ):
            sep = float((ema_f[i] - ema_s[i]) / atr[i])

        strength = None
        if slope is not None and np.isfinite(atr[i]) and atr[i] > 0:
            strength = abs(slope) / float(atr[i])

        # 一致性：回看窗口内 slope 符号与当前同向的占比
        consistency = None
        if slope is not None and i >= p["consistency_window"]:
            signs = []
            for j in range(i - p["consistency_window"] + 1, i + 1):
                k = j - p["slope_lookback"]
                if k >= 0 and np.isfinite(ema_f[j]) and np.isfinite(ema_f[k]):
                    signs.append(np.sign(ema_f[j] - ema_f[k]))
            if signs:
                want = 1 if slope > 0 else -1
                consistency = sum(1 for s in signs if s == want) / len(signs)

        why.update(slope=slope, sep=sep, strength=strength, consistency=consistency)

        if slope is not None and sep is not None:
            strong_dir = (
                strength is not None
                and strength >= p["strength_min"]
                and consistency is not None
                and consistency >= p["consistency_min"]
            )
            if slope > 0 and (sep > p["sep_min_atr"] or strong_dir):
                cycle = "bull"
            elif slope < 0 and (sep < -p["sep_min_atr"] or strong_dir):
                cycle = "bear"
            elif abs(sep) < p["sep_min_atr"] and (
                strength is None or strength < p["strength_min"]
            ):
                # **显式横盘**：既没有间距、也没有强度。两者缺一就不算横盘。
                cycle = "sideways"

        # ---- 波动维（价格代理）----
        triggers = []
        if i >= 1:
            prev_close = c[i - 1]
            if prev_close > 0:
                gap = abs(o[i] / prev_close - 1.0)
                move = abs(c[i] / prev_close - 1.0)
                if gap > VOL["gap_ratio"]:
                    triggers.append(f"gap={gap:.2%}")
                if move > VOL["move_ratio"]:
                    triggers.append(f"move={move:.2%}")
            if np.isfinite(vol_ma[i]) and vol_ma[i] > 0:
                vm = v[i] / vol_ma[i]
                if vm > VOL["volume_multiple"]:
                    triggers.append(f"vol={vm:.1f}x")
            if (
                i >= 2
                and np.isfinite(atr[i])
                and np.isfinite(atr[i - 1])
                and atr[i - 1] > 0
            ):
                aj = atr[i] / atr[i - 1]
                if aj > VOL["atr_jump_ratio"]:
                    triggers.append(f"atr={aj:.1f}x")

        if triggers:
            volatility = "event_spike"
        why["triggers"] = triggers

        out.append((d[i], cycle, volatility, why))
    return out


# --------------------------------------------------------------------------- #
# 真值与对比
# --------------------------------------------------------------------------- #


def truth_days():
    live = list(T.objects.filter(retracted_at__isnull=True))
    ivs = [
        truth.Interval(start=x.start_date, end=x.end_date, regime=BaseRegime(x.regime))
        for x in live
    ]
    return truth.day_regimes(ivs)


def cycle_to_legacy(cycle: str) -> BaseRegime | None:
    return {
        "bull": BaseRegime.UPTREND,
        "bear": BaseRegime.DOWNTREND,
        "sideways": BaseRegime.RANGE,
        "unclear": None,
    }[cycle]


def main():
    rows = load()
    print(f"日线 {len(rows)} 根：{rows[0]['date']} ~ {rows[-1]['date']}")

    new = classify(rows)
    new_by_date = {d: (c, vol, why) for d, c, vol, why in new}

    old = label_series(rows, config.JUDGEMENT)
    old_by_date = {lb.date: lb for lb in old}

    covered = truth_days()
    print(f"真值覆盖 {len(covered)} 天")

    # ---- 逐日对比 ----
    stat = Counter()
    old_ok = new_ok = 0
    old_mis = new_mis = 0
    comparable = 0
    spikes = 0
    spike_in_truth_sideways = 0
    bear_recovered = 0

    for day, regimes in covered.items():
        if len(regimes) > 1:
            continue
        human = next(iter(regimes))
        ol = old_by_date.get(day)
        nn = new_by_date.get(day)
        if ol is None or nn is None:
            continue
        cycle, vol, why = nn
        comparable += 1

        if vol == "event_spike":
            spikes += 1
            if human is BaseRegime.RANGE:
                spike_in_truth_sideways += 1

        # 旧判据
        if ol.regime is not None:
            if ol.regime == human:
                old_ok += 1
            if human in truth.EXTREME and ol.regime == truth.CALM:
                old_mis += 1

        # 新判据（unclear 不进分母）
        mapped = cycle_to_legacy(cycle)
        if mapped is not None:
            if mapped == human:
                new_ok += 1
            if human in truth.EXTREME and mapped == truth.CALM:
                new_mis += 1
            # 缓慢趋势救回：旧判横盘、新判下行、人工也是下行
            if (
                ol.regime is truth.CALM
                and mapped is BaseRegime.DOWNTREND
                and human is BaseRegime.DOWNTREND
            ):
                bear_recovered += 1
        stat[(human.value, cycle, vol)] += 1

    old_denom = sum(
        1
        for d, r in covered.items()
        if len(r) == 1 and old_by_date.get(d) and old_by_date[d].regime is not None
    )
    new_denom = sum(
        1
        for d, r in covered.items()
        if len(r) == 1 and new_by_date.get(d) and new_by_date[d][0] != "unclear"
    )

    print("=" * 62)
    print("对比（同一批真值，只换判定规则）")
    print("=" * 62)
    print(f"  可比天数            {comparable}")
    print(f"  旧分母 / 新分母      {old_denom} / {new_denom}")
    print(
        f"  旧一致              {old_ok}  ({old_ok / old_denom:.1%})"
        if old_denom
        else ""
    )
    print(
        f"  新一致              {new_ok}  ({new_ok / new_denom:.1%})"
        if new_denom
        else ""
    )
    print(f"  旧系统性错向        {old_mis}")
    print(f"  新系统性错向        {new_mis}")
    print()
    print(f"  缓慢趋势救回（旧判箱体→新判下行→人工下行）  {bear_recovered} 天")
    print(
        f"  波动维判 event_spike                      {spikes} 天 ({spikes / comparable:.1%})"
    )
    print(f"  其中人工真值是箱体的                       {spike_in_truth_sideways} 天")
    print()

    # ---- 新维度分布 ----
    print("新结构：周期分布")
    for k, v in Counter(c for _, c, _, _ in new).most_common():
        print(f"    {k:<10} {v:>5} 天")
    print()
    print("新结构：波动分布")
    for k, v in Counter(vol for _, _, vol, _ in new).most_common():
        print(f"    {k:<12} {v:>5} 天")
    print()
    print("**两维同时成立**的样例（证明不互斥）：")
    both = [
        (d, c, vol, why)
        for d, c, vol, why in new
        if vol == "event_spike" and c in ("bull", "bear")
    ]
    print(f"    共 {len(both)} 天属于「有明确周期 + 事件波动」")
    for d, c, vol, why in both[:5]:
        print(f"      {d}  cycle={c}  volatility={vol}  {why.get('triggers')}")
    print()

    # ---- 旧结构做不到的事 ----
    print("旧结构在此期间的对应情形：")
    old_hv = sum(
        1
        for d, r in covered.items()
        if len(r) == 1
        and old_by_date.get(d)
        and old_by_date[d].regime is BaseRegime.HIGH_VOL
    )
    print(f"    旧判 high_vol 的日子：{old_hv} 天（这些日子**周期信息已被丢弃**）")
    print()


if __name__ == "__main__":
    main()
