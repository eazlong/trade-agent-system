"""横盘判据的「相对定义」实验（独立脚本，不改生产代码、不写库）。

## 背景

detect_box_range 在 2518 天里零检出。诊断结论：它找的是**绝对窄幅**箱体
（max_width_pct=0.30），而人工标注的「箱体震荡」是**相对横盘**——
2024-04~09 那段振幅 32.7%，绝对上很大，但相对前一轮牛市是「歇脚」。

## 本脚本试的相对判据

横盘 = 同时满足：
  1. 区间涨跌幅小：|close_t / close_{t-k} - 1| < ret_thr
  2. 期间回撤小：max_drawdown(lookback) < dd_thr
  3. 价格贴着均线：|close / MA120 - 1| < band_thr（可选）

与方向指标组合：
  - 先问横盘（相对判据）→ sideways
  - 否则 close vs MA120 → bull / bear

## 注意判据的方向性

横盘与趋势**争夺同一批日子**。回撤阈值放松 → 更多日子算横盘 → 箱体召回升，
但趋势召回降。所以要看的是**总一致率**，不是单看箱体召回。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app")
os.chdir("/app")

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.prod")
django.setup()

import numpy as np  # noqa: E402

from apps.regime import config, truth  # noqa: E402
from apps.regime.models import DailyCandle, RegimeTruthInterval as T  # noqa: E402
from apps.regime.quant import BaseRegime  # noqa: E402

SYMBOL = config.CANDLES.symbol

rows = list(
    DailyCandle.objects.filter(symbol=SYMBOL).order_by("date").values("date", "close")
)
N = len(rows)
DATES = [r["date"] for r in rows]
CLOSE = np.array([float(r["close"]) for r in rows])
IDX = {d: i for i, d in enumerate(DATES)}


def sma(arr, w):
    out = np.full(len(arr), np.nan)
    for i in range(len(arr)):
        if i + 1 >= w:
            out[i] = arr[i - w + 1 : i + 1].mean()
    return out


MA120 = sma(CLOSE, 120)


def rolling_max_dd(lookback):
    """过去 lookback 天内的最大回撤（正数，0.15 = 15%）。"""
    out = np.full(N, np.nan)
    for i in range(N):
        if i + 1 < lookback:
            continue
        seg = CLOSE[i - lookback + 1 : i + 1]
        peak = np.maximum.accumulate(seg)
        dd = (seg / peak - 1.0).min()
        out[i] = -float(dd)
    return out


def rolling_ret(lookback):
    out = np.full(N, np.nan)
    for i in range(N):
        j = i - lookback
        if j < 0 or CLOSE[j] <= 0:
            continue
        out[i] = CLOSE[i] / CLOSE[j] - 1.0
    return out


RET60 = rolling_ret(60)
RET90 = rolling_ret(90)
DD60 = rolling_max_dd(60)
DD90 = rolling_max_dd(90)

live = list(T.objects.filter(retracted_at__isnull=True))
IVS = [
    truth.Interval(start=x.start_date, end=x.end_date, regime=BaseRegime(x.regime))
    for x in live
]
COVERED = truth.day_regimes(IVS)
TRUTH = {d: next(iter(r)) for d, r in COVERED.items() if len(r) == 1}

LEGACY = {
    "bull": BaseRegime.UPTREND,
    "bear": BaseRegime.DOWNTREND,
    "sideways": BaseRegime.RANGE,
}

print(f"日线 {N} 根  {DATES[0]} ~ {DATES[-1]}")
print(f"真值可比日 {len(TRUTH)}")
print()


def score(cycles, name, show=True):
    ok = mis = denom = unc = 0
    per = {}
    for d, human in TRUTH.items():
        c = cycles.get(d, "unclear")
        denom += 1
        k = human.display
        per.setdefault(k, [0, 0])
        per[k][1] += 1
        if c == "unclear":
            unc += 1
            continue
        m = LEGACY[c]
        if m == human:
            ok += 1
            per[k][0] += 1
        if human in truth.EXTREME and m == truth.CALM:
            mis += 1
    rate = ok / denom if denom else 0
    if show:
        detail = "  ".join(
            f"{k}:{v[0]}/{v[1]}({v[0] / v[1]:.0%})" for k, v in sorted(per.items())
        )
        print(f"  {name:<40} {rate:>7.1%}  unc={unc:>4} mis={mis:>3}   {detail}")
    return rate, per, unc, mis


def combo(ret_thr, dd_thr, ret_arr, dd_arr, ma_tol=0.0):
    cycles = {}
    for i, d in enumerate(DATES):
        if np.isfinite(ret_arr[i]) and np.isfinite(dd_arr[i]):
            if abs(ret_arr[i]) < ret_thr and dd_arr[i] < dd_thr:
                cycles[d] = "sideways"
                continue
        if not np.isfinite(MA120[i]) or MA120[i] <= 0:
            cycles[d] = "unclear"
            continue
        dev = CLOSE[i] / MA120[i] - 1.0
        if dev > ma_tol:
            cycles[d] = "bull"
        elif dev < -ma_tol:
            cycles[d] = "bear"
        else:
            cycles[d] = "unclear"
    return cycles


print("=" * 120)
print("基线：纯方向（无横盘维）")
print("=" * 120)
BASE = combo(0.0, 0.0, RET60, DD60)
score(BASE, "纯方向 close vs MA120")

print()
print("=" * 120)
print("60 日窗口：收益率阈值 × 回撤阈值")
print("=" * 120)
print(
    f"  {'ret':>6} {'dd':>6} {'一致率':>8} {'箱体召回':>10} {'上行':>8} {'下行':>8} {'unc':>5}"
)
best = None
for ret_thr in [0.08, 0.10, 0.12, 0.15, 0.20, 0.25]:
    for dd_thr in [0.10, 0.12, 0.15, 0.18, 0.20, 0.25]:
        cyc = combo(ret_thr, dd_thr, RET60, DD60)
        rate, per, unc, mis = score(cyc, "", show=False)
        box = per.get("箱体震荡", [0, 1])
        up = per.get("上行趋势", [0, 1])
        dn = per.get("下行趋势", [0, 1])
        mark = ""
        if best is None or rate > best[0]:
            best = (rate, ret_thr, dd_thr)
            mark = " <<"
        print(
            f"  {ret_thr:>6.0%} {dd_thr:>6.0%} {rate:>7.1%} "
            f"{box[0]:>4}/{box[1]:<5} {up[0]:>3}/{up[1]:<4} {dn[0]:>3}/{dn[1]:<4} {unc:>5}{mark}"
        )

print()
print(f"60 日窗口最优：ret={best[1]:.0%} dd={best[2]:.0%} → {best[0]:.1%}")
print()

print("=" * 120)
print("90 日窗口：收益率阈值 × 回撤阈值")
print("=" * 120)
print(
    f"  {'ret':>6} {'dd':>6} {'一致率':>8} {'箱体召回':>10} {'上行':>8} {'下行':>8} {'unc':>5}"
)
best90 = None
for ret_thr in [0.10, 0.15, 0.20, 0.25, 0.30]:
    for dd_thr in [0.12, 0.15, 0.18, 0.20, 0.25, 0.30]:
        cyc = combo(ret_thr, dd_thr, RET90, DD90)
        rate, per, unc, mis = score(cyc, "", show=False)
        box = per.get("箱体震荡", [0, 1])
        up = per.get("上行趋势", [0, 1])
        dn = per.get("下行趋势", [0, 1])
        mark = ""
        if best90 is None or rate > best90[0]:
            best90 = (rate, ret_thr, dd_thr)
            mark = " <<"
        print(
            f"  {ret_thr:>6.0%} {dd_thr:>6.0%} {rate:>7.1%} "
            f"{box[0]:>4}/{box[1]:<5} {up[0]:>3}/{up[1]:<4} {dn[0]:>3}/{dn[1]:<4} {unc:>5}{mark}"
        )

print()
print(f"90 日窗口最优：ret={best90[1]:.0%} dd={best90[2]:.0%} → {best90[0]:.1%}")
print()

print("=" * 120)
print("两个窗口最优参数的详细对比")
print("=" * 120)
if best[0] >= best90[0]:
    ch = combo(best[1], best[2], RET60, DD60)
    score(ch, f"60日 ret={best[1]:.0%} dd={best[2]:.0%}")
else:
    ch = combo(best90[1], best90[2], RET90, DD90)
    score(ch, f"90日 ret={best90[1]:.0%} dd={best90[2]:.0%}")
score(BASE, "纯方向（对照）")
