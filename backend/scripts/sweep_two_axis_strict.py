"""unclear 进分母后的边界扫描（独立脚本，不改生产代码、不写库）。

## 口径变更

上一轮把 unclear 排除在分母外，一致率 66.2%。本脚本改成 **unclear 按不匹配计入分母**，
这是更严格的验收口径：机制**说不出结论**也是一种失败，不能靠"弃权"换高一致率。

## 因此要回答两个新问题

1. unclear 到底由什么造成？是预热期（数据不足，合理）还是信号冲突（判定设计不足）？
2. 在新口径下，参数最优点在哪？此时"减少 unclear"成为一等目标。
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

EMA_F = _ema(CLOSE, 20)
EMA_S = _ema(CLOSE, 60)
ATR_14 = _atr(HIGH, LOW, CLOSE, period=14)

SLOPE_K = 5
SLOPE = np.full(N, np.nan)
SEP = np.full(N, np.nan)
STRENGTH = np.full(N, np.nan)
for i in range(N):
    j = i - SLOPE_K
    if j >= 0 and np.isfinite(EMA_F[i]) and np.isfinite(EMA_F[j]):
        SLOPE[i] = EMA_F[i] - EMA_F[j]
    if np.isfinite(ATR_14[i]) and ATR_14[i] > 0:
        if np.isfinite(EMA_F[i]) and np.isfinite(EMA_S[i]):
            SEP[i] = (EMA_F[i] - EMA_S[i]) / ATR_14[i]
        if np.isfinite(SLOPE[i]):
            STRENGTH[i] = abs(SLOPE[i]) / ATR_14[i]

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

LEGACY = {
    "bull": BaseRegime.UPTREND,
    "bear": BaseRegime.DOWNTREND,
    "sideways": BaseRegime.RANGE,
}

print(f"日线 {N} 根  {DATES[0]} ~ {DATES[-1]}")
print(f"真值可比日 {len(TRUTH)}")
print()


def classify(sep_min, strength_min, cons_min):
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


def score(cycles):
    """unclear **进分母**，按不匹配计。"""
    ok = mis = denom = 0
    unclear_n = 0
    for d, human in TRUTH.items():
        c = cycles.get(d, "unclear")
        denom += 1
        if c == "unclear":
            unclear_n += 1
            continue
        mapped = LEGACY[c]
        if mapped == human:
            ok += 1
        if human in truth.EXTREME and mapped == truth.CALM:
            mis += 1
    rate = ok / denom if denom else 0.0
    return ok, denom, rate, mis, unclear_n


# ---------- 1. 诊断 unclear 成因 ----------
print("=" * 78)
print("unclear 成因诊断（基线 sep=0.15 strength=0.10 cons=0.60）")
print("=" * 78)
base = classify(0.15, 0.10, 0.60)
reasons = Counter()
warmup = 0
first_valid = None
for i, d in enumerate(DATES):
    if np.isfinite(SLOPE[i]) and np.isfinite(SEP[i]) and np.isfinite(STRENGTH[i]):
        first_valid = i
        break
for d, human in TRUTH.items():
    c = base.get(d, "unclear")
    if c != "unclear":
        continue
    i = DATES.index(d)
    if not np.isfinite(SLOPE[i]) or not np.isfinite(SEP[i]):
        reasons["数据不足（预热未满）"] += 1
    elif not np.isfinite(STRENGTH[i]):
        reasons["ATR 为 0 或无法算强度"] += 1
    elif not np.isfinite(CONSIST[i]):
        reasons["一致性窗口未满"] += 1
    else:
        s, sp, st = SLOPE[i], SEP[i], STRENGTH[i]
        if abs(sp) >= 0.15 and st < 0.10:
            reasons["间距够但强度不足（方向可疑）"] += 1
        elif abs(sp) < 0.15 and st >= 0.10:
            reasons["强度够但一致性不足（有动能无共识）"] += 1
        else:
            reasons["其他"] += 1
for k, v in reasons.most_common():
    print(f"  {k:<34} {v:>4} 天")
tot = sum(reasons.values())
print(f"  {'合计':<34} {tot:>4} 天")
print(
    f"  首个可判定日索引 {first_valid} ({DATES[first_valid] if first_valid else '-'})"
)
print()

# ---------- 2. 单参数敏感度（unclear 进分母） ----------
print("=" * 78)
print("单参数敏感度（unclear 进分母）")
print("=" * 78)

print("\n[sep_min]")
print(f"  {'值':>6} {'一致率':>8} {'错向':>6} {'unclear':>8}")
for v in [0.40, 0.30, 0.25, 0.20, 0.15, 0.12, 0.10, 0.05, 0.02]:
    r = score(classify(v, 0.10, 0.60))
    print(f"  {v:>6.2f} {r[2]:>7.1%} {r[3]:>6} {r[4]:>8}")

print("\n[strength_min]")
print(f"  {'值':>6} {'一致率':>8} {'错向':>6} {'unclear':>8}")
for v in [0.30, 0.25, 0.20, 0.15, 0.10, 0.07, 0.05, 0.03, 0.01]:
    r = score(classify(0.15, v, 0.60))
    print(f"  {v:>6.2f} {r[2]:>7.1%} {r[3]:>6} {r[4]:>8}")

print("\n[consistency_min]")
print(f"  {'值':>6} {'一致率':>8} {'错向':>6} {'unclear':>8}")
for v in [0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20]:
    r = score(classify(0.15, 0.10, v))
    print(f"  {v:>6.2f} {r[2]:>7.1%} {r[3]:>6} {r[4]:>8}")

# ---------- 3. 三维网格找最优 ----------
print()
print("=" * 78)
print("网格扫描（unclear 进分母，目标：一致率最大且错向为 0）")
print("=" * 78)
best = []
for sep in [0.30, 0.25, 0.20, 0.15, 0.12, 0.10]:
    for st in [0.20, 0.15, 0.12, 0.10, 0.07, 0.05]:
        for cs in [0.80, 0.70, 0.60, 0.50, 0.40]:
            r = score(classify(sep, st, cs))
            best.append((sep, st, cs, r))
best.sort(key=lambda x: (-x[3][2], x[3][3]))
print(f"  {'sep':>5} {'str':>5} {'cons':>5} {'一致率':>8} {'错向':>5} {'unclear':>8}")
for sep, st, cs, r in best[:15]:
    print(f"  {sep:>5.2f} {st:>5.2f} {cs:>5.2f} {r[2]:>7.1%} {r[3]:>5} {r[4]:>8}")

print()
print("对比：旧结构在 unclear 进分母口径下")
from apps.regime.quant import label_series  # noqa: E402

old_labels = {
    lb.date: lb
    for lb in label_series(
        [
            {"date": DATES[i], "high": HIGH[i], "low": LOW[i], "close": CLOSE[i]}
            for i in range(N)
        ],
        config.JUDGEMENT,
    )
}
o_ok = o_mis = o_denom = 0
for d, human in TRUTH.items():
    lb = old_labels.get(d)
    o_denom += 1
    if lb is None or lb.regime is None:
        continue
    if lb.regime == human:
        o_ok += 1
    if human in truth.EXTREME and lb.regime == truth.CALM:
        o_mis += 1
print(f"  旧结构：一致 {o_ok}/{o_denom} = {o_ok / o_denom:.1%}  错向 {o_mis}")
