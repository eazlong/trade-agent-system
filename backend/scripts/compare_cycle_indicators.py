"""周期维指标对照实验（独立脚本，不改生产代码、不写库）。

## 起因

用户确认：标注「上行趋势」的依据是「**价格整体在涨**」，不是 EMA 交叉形态。
这解释了上一轮的诊断结果——492 天 unclear 里有 313 天真值是「上行趋势」，而机制
因「EMA20 与 EMA60 排列方向」与「短期斜率」背离而弃权。

**两套指标回答的不是同一个问题**：
  - EMA 交叉形态 → 「均线排列是否已经转多」（滞后、看形态）
  - 价格相对位置 → 「价格现在比过去高还是低」（直接、看水平）

用户标注用的是后者。本脚本把候选指标并排测一遍。

## 候选指标

1. 现有（EMA20/60 + 斜率 + 间距）
2. close vs MA200（价格在长期均线上/下）
3. close vs MA120
4. MA200 斜率（长均线自身方向）
5. 长周期收益：close / close.shift(90) - 1
6. 组合：位置 + 长均线斜率
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
from apps.strategy_engine.indicators import atr as _atr  # noqa: E402
from apps.strategy_engine.indicators import ema as _ema  # noqa: E402

SYMBOL = config.CANDLES.symbol


def load():
    rows = list(
        DailyCandle.objects.filter(symbol=SYMBOL)
        .order_by("date")
        .values("date", "open", "high", "low", "close", "volume")
    )
    dates = [r["date"] for r in rows]
    c = np.array([float(r["close"]) for r in rows])
    h = np.array([float(r["high"]) for r in rows])
    lo = np.array([float(r["low"]) for r in rows])
    return dates, c, h, lo


DATES, CLOSE, HIGH, LOW = load()
N = len(CLOSE)
IDX = {d: i for i, d in enumerate(DATES)}


def sma(arr, w):
    out = np.full(len(arr), np.nan)
    for i in range(len(arr)):
        if i + 1 >= w:
            out[i] = arr[i - w + 1 : i + 1].mean()
    return out


MA120 = sma(CLOSE, 120)
MA200 = sma(CLOSE, 200)
MA365 = sma(CLOSE, 365)

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


def score(cycles, name):
    ok = mis = denom = unc = 0
    per_truth = {}
    for d, human in TRUTH.items():
        c = cycles.get(d, "unclear")
        denom += 1
        key = human.display
        per_truth.setdefault(key, [0, 0])
        per_truth[key][1] += 1
        if c == "unclear":
            unc += 1
            continue
        m = LEGACY[c]
        if m == human:
            ok += 1
            per_truth[key][0] += 1
        if human in truth.EXTREME and m == truth.CALM:
            mis += 1
    rate = ok / denom if denom else 0
    detail = "  ".join(
        f"{k}:{v[0]}/{v[1]}({v[0] / v[1]:.0%})" for k, v in sorted(per_truth.items())
    )
    print(f"  {name:<30} {rate:>7.1%}  unc={unc:>4}  mis={mis:>3}   {detail}")
    return rate


print(f"日线 {N} 根  {DATES[0]} ~ {DATES[-1]}")
print(f"真值可比日 {len(TRUTH)}")
print()
print("=" * 100)
print("候选指标对照（unclear 进分母；detail 为各真值档位的召回率）")
print("=" * 100)


# ---------- 指标 2/3：价格相对均线位置 ----------
def by_position(ma, tol=0.0):
    """close 在 MA 上/下 → bull/bear；太近算 sideways。"""
    cycles = {}
    for i, d in enumerate(DATES):
        if not np.isfinite(ma[i]) or ma[i] <= 0:
            cycles[d] = "unclear"
            continue
        dev = CLOSE[i] / ma[i] - 1.0
        if dev > tol:
            cycles[d] = "bull"
        elif dev < -tol:
            cycles[d] = "bear"
        else:
            cycles[d] = "sideways"
    return cycles


for w, ma in [(120, MA120), (200, MA200), (365, MA365)]:
    score(by_position(ma, 0.0), f"close vs MA{w}（>0 多 / <0 空）")


# ---------- 指标 4：长周期收益 ----------
def by_return(lookback, thr=0.0):
    cycles = {}
    for i, d in enumerate(DATES):
        j = i - lookback
        if j < 0 or CLOSE[j] <= 0:
            cycles[d] = "unclear"
            continue
        r = CLOSE[i] / CLOSE[j] - 1.0
        if r > thr:
            cycles[d] = "bull"
        elif r < -thr:
            cycles[d] = "bear"
        else:
            cycles[d] = "sideways"
    return cycles


for lb in [30, 60, 90, 120, 180]:
    score(by_return(lb), f"{lb}日收益（>0 多 / <0 空）")


# ---------- 指标 5：长均线斜率 ----------
def by_ma_slope(ma, lookback=20):
    cycles = {}
    for i, d in enumerate(DATES):
        j = i - lookback
        if j < 0 or not np.isfinite(ma[i]) or not np.isfinite(ma[j]):
            cycles[d] = "unclear"
            continue
        s = ma[i] - ma[j]
        if s > 0:
            cycles[d] = "bull"
        elif s < 0:
            cycles[d] = "bear"
        else:
            cycles[d] = "sideways"
    return cycles


score(by_ma_slope(MA200, 20), "MA200 20日斜率")
score(by_ma_slope(MA120, 20), "MA120 20日斜率")


# ---------- 组合：位置 + 斜率 ----------
def by_combo(ma, lookback=20, tol=0.0):
    cycles = {}
    for i, d in enumerate(DATES):
        j = i - lookback
        if j < 0 or not np.isfinite(ma[i]) or not np.isfinite(ma[j]) or ma[i] <= 0:
            cycles[d] = "unclear"
            continue
        dev = CLOSE[i] / ma[i] - 1.0
        slope = ma[i] - ma[j]
        if dev > tol and slope > 0:
            cycles[d] = "bull"
        elif dev < -tol and slope < 0:
            cycles[d] = "bear"
        elif abs(dev) <= tol and abs(slope) < 1e-9:
            cycles[d] = "sideways"
        else:
            cycles[d] = "unclear"
    return cycles


score(by_combo(MA200), "MA200 位置+斜率（都同向才算）")

# ---------- 现有结构（对照） ----------
EMA_F = _ema(CLOSE, 20)
EMA_S = _ema(CLOSE, 60)
ATR_14 = _atr(HIGH, LOW, CLOSE, period=14)
SEP = np.full(N, np.nan)
for i in range(N):
    if (
        np.isfinite(ATR_14[i])
        and ATR_14[i] > 0
        and np.isfinite(EMA_F[i])
        and np.isfinite(EMA_S[i])
    ):
        SEP[i] = (EMA_F[i] - EMA_S[i]) / ATR_14[i]


def by_ema_cross(sep_min=0.10):
    cycles = {}
    for i, d in enumerate(DATES):
        if not np.isfinite(SEP[i]):
            cycles[d] = "unclear"
            continue
        if SEP[i] > sep_min:
            cycles[d] = "bull"
        elif SEP[i] < -sep_min:
            cycles[d] = "bear"
        else:
            cycles[d] = "sideways"
    return cycles


score(by_ema_cross(), "EMA20/60 排列（现有核心）")

print()
print("=" * 100)
print("价格位置法：阈值敏感度（close/MA200 偏离度）")
print("=" * 100)
for tol in [0.0, 0.02, 0.05, 0.08, 0.12, 0.15, 0.20]:
    score(by_position(MA200, tol), f"MA200 偏离 >±{tol:.0%}")

print()
print("=" * 100)
print("长期收益法：阈值敏感度（180日收益）")
print("=" * 100)
for thr in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]:
    score(by_return(180, thr), f"180日收益 >±{thr:.0%}")
