"""方向指标 + 箱体检测 的组合实验（独立脚本，不改生产代码、不写库）。

## 要验证的假设

上一轮对照实验发现所有方向指标在「箱体震荡」上召回率都是 0%：
  - close vs MA120 = 65.7%，但箱体 0/302
  - 60日收益 = 62.4%，箱体 0/302

而真值里箱体占 302 天（12.3%），正是达不到 80% 的缺口。

quant.py 的 docstring 承认：箱体判定（detect_box_range）**不参与**档位归属，
检不检出都落到兜底档。本脚本把它接回来，让 sideways 成为**显式检出**的结果。

## 两条规则

  1. 先问箱体：若窗口内检出成形箱体 → sideways（不再看方向）
  2. 否则看方向：close 在 MA120 上/下 → bull/bear
  3. 都不满足 → unclear
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
from apps.strategy_engine.indicators import detect_box_range  # noqa: E402

SYMBOL = config.CANDLES.symbol


def load():
    return list(
        DailyCandle.objects.filter(symbol=SYMBOL)
        .order_by("date")
        .values("date", "open", "high", "low", "close", "volume")
    )


ROWS = load()
N = len(ROWS)
DATES = [r["date"] for r in ROWS]
CLOSE = np.array([float(r["close"]) for r in ROWS])
IDX = {d: i for i, d in enumerate(DATES)}


def sma(arr, w):
    out = np.full(len(arr), np.nan)
    for i in range(len(arr)):
        if i + 1 >= w:
            out[i] = arr[i - w + 1 : i + 1].mean()
    return out


MA120 = sma(CLOSE, 120)

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


def box_flags(win):
    """每天是否在滚动窗口内检出成形箱体。"""
    BOX = config.BOX.kwargs()
    flags = np.zeros(N, dtype=bool)
    fail = 0
    for i in range(N):
        if i + 1 < win:
            continue
        klines = [
            {
                "high": ROWS[j]["high"],
                "low": ROWS[j]["low"],
                "close": ROWS[j]["close"],
            }
            for j in range(i - win + 1, i + 1)
        ]
        try:
            res = detect_box_range(klines, **BOX)
        except Exception:
            fail += 1
            continue
        if res.get("upper") is not None and res.get("lower") is not None:
            flags[i] = True
    return flags, fail


def score(cycles, name):
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
    detail = "  ".join(
        f"{k}:{v[0]}/{v[1]}({v[0] / v[1]:.0%})" for k, v in sorted(per.items())
    )
    print(f"  {name:<36} {rate:>7.1%}  unc={unc:>4} mis={mis:>3}   {detail}")
    return rate


print("检测箱体（滚动窗口 30 根，参数用 config.BOX）...")
FLAGS30, FAIL30 = box_flags(30)
print(f"  检出箱体 {FLAGS30.sum()} 天（{FLAGS30.sum() / N:.1%}），调用失败 {FAIL30}")
print()


def combo(flags, box_first=True, ma_tol=0.0):
    cycles = {}
    for i, d in enumerate(DATES):
        if box_first and flags[i]:
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
        elif flags[i]:
            cycles[d] = "sideways"
        else:
            cycles[d] = "unclear"
    return cycles


print("=" * 108)
print("组合实验（unclear 进分母）")
print("=" * 108)
score(combo(FLAGS30, box_first=False), "A 纯方向（close vs MA120）")
score(combo(FLAGS30, box_first=True), "B 箱体优先 + 方向")
score(
    combo(np.zeros(N, dtype=bool), box_first=False),
    "B0 纯方向（无箱体维，对照）",
)

print()
print("=" * 108)
print("箱体检出的准确性：只有它判 sideways 的那些天，真值是什么")
print("=" * 108)
box_days = [d for d in TRUTH if FLAGS30[IDX[d]]]

print(f"  箱体检出且落在真值区间的日子：{len(box_days)} 天")
for k, v in Counter(TRUTH[d].display for d in box_days).most_common():
    print(f"    真值={k}: {v} 天 ({v / len(box_days):.0%})")

print()
print("=" * 108)
print("箱体优先的代价：被判 sideways 却真值不是箱体的天")
print("=" * 108)
wrong_box = [d for d in TRUTH if FLAGS30[IDX[d]] and TRUTH[d] is not BaseRegime.RANGE]
print(f"  {len(wrong_box)} 天")
for k, v in Counter(TRUTH[d].display for d in wrong_box).most_common():
    print(f"    真值={k}: {v} 天")

print()
print("=" * 108)
print("窗口大小敏感度（箱体优先 + 方向）")
print("=" * 108)
for win in [20, 25, 30, 40]:
    flags, fail = box_flags(win)
    cyc = {}
    for i, d in enumerate(DATES):
        if flags[i]:
            cyc[d] = "sideways"
        elif not np.isfinite(MA120[i]) or MA120[i] <= 0:
            cyc[d] = "unclear"
        else:
            cyc[d] = "bull" if CLOSE[i] > MA120[i] else "bear"
    score(cyc, f"窗口 {win} 根（检出 {flags.sum()} 天，失败 {fail}）")
