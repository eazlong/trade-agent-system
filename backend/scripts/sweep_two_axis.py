"""二维结构参数边界扫描（**独立脚本，不改生产代码、不写库**）。

回答三个问题：
1. 每个参数对「一致率 / 错向 / unclear 占比」的敏感度如何？
2. 有没有帕累托前沿（一致率↑ 且 错向↓ 且 分母不塌）？
3. unclear 的口径（进不进分母）如何影响结论？

## 为什么单参数扫描不够

旧结构调参的教训（实测）：调 high_vol_quantile 让一致率涨 21%，错向同时变差；
调 sep_min_atr 到 0 只多对 67 天。**任何单参数调整都只是在一致率与错向之间搬动。**
所以这里同时扫三个数，并显式报告 unclear 占比——分母塌掉时高一致率是假的。
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
    o = np.array([float(r["open"]) for r in rows])
    h = np.array([float(r["high"]) for r in rows])
    lo = np.array([float(r["low"]) for r in rows])
    c = np.array([float(r["close"]) for r in rows])
    v = np.array([float(r["volume"]) for r in rows])
    return dates, o, h, lo, c, v


DATES, OPEN, HIGH, LOW, CLOSE, VOLUME = load()
N = len(CLOSE)

# 指标只算一次（与判定参数无关的部分）
EMA_F = _ema(CLOSE, 20)
EMA_S = _ema(CLOSE, 60)
ATR_14 = _atr(HIGH, LOW, CLOSE, period=14)

# 预计算斜率、间距、强度（依赖 lookback，这里固定 5）
SLOPE_K = 5
SLOPE = np.full(N, np.nan)
for i in range(N):
    j = i - SLOPE_K
    if j >= 0 and np.isfinite(EMA_F[i]) and np.isfinite(EMA_F[j]):
        SLOPE[i] = EMA_F[i] - EMA_F[j]

SEP = np.full(N, np.nan)
for i in range(N):
    if (
        np.isfinite(ATR_14[i])
        and ATR_14[i] > 0
        and np.isfinite(EMA_F[i])
        and np.isfinite(EMA_S[i])
    ):
        SEP[i] = (EMA_F[i] - EMA_S[i]) / ATR_14[i]

STRENGTH = np.full(N, np.nan)
for i in range(N):
    if np.isfinite(SLOPE[i]) and np.isfinite(ATR_14[i]) and ATR_14[i] > 0:
        STRENGTH[i] = abs(SLOPE[i]) / ATR_14[i]

# 一致性（窗口 20）
CONS_W = 20
CONSIST = np.full(N, np.nan)
for i in range(N):
    if not np.isfinite(SLOPE[i]) or i < CONS_W:
        continue
    want = 1 if SLOPE[i] > 0 else -1
    signs = []
    for j in range(i - CONS_W + 1, i + 1):
        k = j - SLOPE_K
        if k >= 0 and np.isfinite(EMA_F[j]) and np.isfinite(EMA_F[k]):
            signs.append(np.sign(EMA_F[j] - EMA_F[k]))
    if signs:
        CONSIST[i] = sum(1 for s in signs if s == want) / len(signs)

# 波动代理
VOL_MA20 = np.full(N, np.nan)
for i in range(N):
    if i + 1 >= 20:
        VOL_MA20[i] = VOLUME[i - 19 : i + 1].mean()

GAP = np.full(N, np.nan)
MOVE = np.full(N, np.nan)
VMULT = np.full(N, np.nan)
AJUMP = np.full(N, np.nan)
for i in range(N):
    if i >= 1 and CLOSE[i - 1] > 0:
        GAP[i] = abs(OPEN[i] / CLOSE[i - 1] - 1.0)
        MOVE[i] = abs(CLOSE[i] / CLOSE[i - 1] - 1.0)
    if np.isfinite(VOL_MA20[i]) and VOL_MA20[i] > 0:
        VMULT[i] = VOLUME[i] / VOL_MA20[i]
    if (
        i >= 1
        and np.isfinite(ATR_14[i])
        and np.isfinite(ATR_14[i - 1])
        and ATR_14[i - 1] > 0
    ):
        AJUMP[i] = ATR_14[i] / ATR_14[i - 1]

# 真值
live = list(T.objects.filter(retracted_at__isnull=True))
IVS = [
    truth.Interval(start=x.start_date, end=x.end_date, regime=BaseRegime(x.regime))
    for x in live
]
COVERED = truth.day_regimes(IVS)
TRUTH = {}
for d, regs in COVERED.items():
    if len(regs) == 1:
        TRUTH[d] = next(iter(regs))

print(f"日线 {N} 根  {DATES[0]} ~ {DATES[-1]}")
print(f"真值可比日 {len(TRUTH)}")
print()


def classify(sep_min, strength_min, cons_min):
    """返回 {date: cycle}。"""
    out = {}
    for i in range(N):
        cycle = "unclear"
        s, sp, st, cs = SLOPE[i], SEP[i], STRENGTH[i], CONSIST[i]
        if np.isfinite(s) and np.isfinite(sp):
            strong_dir = (
                np.isfinite(st)
                and st >= strength_min
                and np.isfinite(cs)
                and cs >= cons_min
            )
            if s > 0 and (sp > sep_min or strong_dir):
                cycle = "bull"
            elif s < 0 and (sp < -sep_min or strong_dir):
                cycle = "bear"
            elif abs(sp) < sep_min and (not np.isfinite(st) or st < strength_min):
                cycle = "sideways"
        out[DATES[i]] = cycle
    return out


LEGACY = {
    "bull": BaseRegime.UPTREND,
    "bear": BaseRegime.DOWNTREND,
    "sideways": BaseRegime.RANGE,
}


def score(cycles):
    ok = mis = denom = 0
    unclear_in_truth = 0
    for d, human in TRUTH.items():
        c = cycles.get(d, "unclear")
        if c == "unclear":
            unclear_in_truth += 1
            continue
        denom += 1
        mapped = LEGACY[c]
        if mapped == human:
            ok += 1
        if human in truth.EXTREME and mapped == truth.CALM:
            mis += 1
    rate = ok / denom if denom else 0.0
    return ok, denom, rate, mis, unclear_in_truth


base = score(classify(0.35, 0.15, 0.60))
print("=" * 74)
print("基线（sep=0.35 strength=0.15 cons=0.60）")
print("=" * 74)
print(
    f"  一致 {base[0]}/{base[1]} = {base[2]:.1%}   错向 {base[3]}   unclear(真值内) {base[4]}"
)
print()

# ---------- 单参数敏感度 ----------
print("=" * 74)
print("单参数敏感度（其余保持基线）")
print("=" * 74)

print("\n[sep_min]")
print(f"  {'值':>6} {'分母':>6} {'一致率':>8} {'错向':>6} {'unclear':>8}")
for v in [0.60, 0.50, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.10]:
    r = score(classify(v, 0.15, 0.60))
    print(f"  {v:>6.2f} {r[1]:>6} {r[2]:>7.1%} {r[3]:>6} {r[4]:>8}")

print("\n[strength_min]")
print(f"  {'值':>6} {'分母':>6} {'一致率':>8} {'错向':>6} {'unclear':>8}")
for v in [0.30, 0.25, 0.20, 0.15, 0.12, 0.10, 0.08, 0.05, 0.02]:
    r = score(classify(0.35, v, 0.60))
    print(f"  {v:>6.2f} {r[1]:>6} {r[2]:>7.1%} {r[3]:>6} {r[4]:>8}")

print("\n[consistency_min]")
print(f"  {'值':>6} {'分母':>6} {'一致率':>8} {'错向':>6} {'unclear':>8}")
for v in [0.90, 0.80, 0.70, 0.60, 0.55, 0.50, 0.40]:
    r = score(classify(0.35, 0.15, v))
    print(f"  {v:>6.2f} {r[1]:>6} {r[2]:>7.1%} {r[3]:>6} {r[4]:>8}")

# ---------- 二维网格：找帕累托 ----------
print()
print("=" * 74)
print("网格扫描（sep × strength），cons 固定 0.60")
print("=" * 74)
results = []
for sep in [0.40, 0.35, 0.30, 0.25, 0.20, 0.15]:
    for st in [0.25, 0.20, 0.15, 0.12, 0.10, 0.08]:
        r = score(classify(sep, st, 0.60))
        results.append((sep, st, r))


# 帕累托：一致率高、错向低、分母大
def dominates(a, b):
    ax, bx = a[2], b[2]
    return (
        ax[1] >= bx[1]
        and ax[2] >= bx[2]
        and ax[3] <= bx[3]
        and (ax[1] > bx[1] or ax[2] > bx[2] or ax[3] < bx[3])
    )


front = [r for r in results if not any(dominates(o, r) for o in results if o is not r)]
front.sort(key=lambda r: -r[2][2])
print(f"\n帕累托前沿 {len(front)} 组：")
print(f"  {'sep':>5} {'strength':>9} {'分母':>6} {'一致率':>8} {'错向':>6}")
for sep, st, r in front[:12]:
    print(f"  {sep:>5.2f} {st:>9.2f} {r[1]:>6} {r[2]:>7.1%} {r[3]:>6}")

# ---------- 波动维边界 ----------
print()
print("=" * 74)
print("波动维边界：不同代理阈值下的 event_spike 天数")
print("=" * 74)
print(f"  {'move':>6} {'gap':>6} {'volx':>6} {'atr':>6} {'spike天':>8} {'占比':>7}")
for mv in [0.10, 0.07, 0.05, 0.04, 0.03]:
    for gm, vx, aj in [(0.03, 2.5, 1.8), (0.02, 2.0, 1.5), (0.015, 1.5, 1.3)]:
        cnt = 0
        for i in range(N):
            if (
                (np.isfinite(MOVE[i]) and MOVE[i] > mv)
                or (np.isfinite(GAP[i]) and GAP[i] > gm)
                or (np.isfinite(VMULT[i]) and VMULT[i] > vx)
                or (np.isfinite(AJUMP[i]) and AJUMP[i] > aj)
            ):
                cnt += 1
        print(f"  {mv:>6.2f} {gm:>6.3f} {vx:>6.1f} {aj:>6.1f} {cnt:>8} {cnt / N:>6.1%}")
