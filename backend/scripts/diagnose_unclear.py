"""unclear 归因诊断 v2（独立脚本，不改生产代码、不写库）。

## 上一版为什么失败

上一版把 65% 的 unclear 归进「其他」——分支没覆盖真实情况。原因是我按「sep 和
strength **各自**是否够」来分，但判定路径是 **「sep 够 OR 强度够」**，所以一天
unclear 的前提是：**两条路径都没走通**。正确的问题不是「哪个够」，而是
「两条路径为什么都不能成立」。

## v2 的归因方式

对每个 unclear 日，枚举它**为什么没被三条规则中任何一条接住**：

    bull  ⟸ slope>0 且 (sep>θ 或 strong_dir)
    bear  ⟸ slope<0 且 (sep<-θ 或 strong_dir)
    sideways ⟸ |sep|<θ 且 strength<σ

于是 unclear 只有四种成因（互斥且穷尽）：

    A. 数据缺失        — slope/sep/strength 有 NaN（预热期）
    B. 方向与间距冲突  — |sep| 够大，但符号与 slope 相反 → 三条规则都不匹配
    C. 强度够但一致性不够，且间距不够 → strong_dir 假，|sep|<θ 也不成立（因为 sep 够）
    D. 间距够且强度够、方向一致，但一致性不够 → 前两条对，strong_dir 假，
                        而 sideways 要求 strength<σ 也不成立
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
IDX = {d: i for i, d in enumerate(DATES)}

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
TRUTH = {d: next(iter(r)) for d, r in COVERED.items() if len(r) == 1}

LEGACY = {
    "bull": BaseRegime.UPTREND,
    "bear": BaseRegime.DOWNTREND,
    "sideways": BaseRegime.RANGE,
}


def diagnose(i, sep_min, strength_min, cons_min):
    """返回 (cycle, 成因标签)。成因标签只在 cycle=='unclear' 时有意义。"""
    s, sp, st, cs = SLOPE[i], SEP[i], STRENGTH[i], CONSIST[i]

    if not np.isfinite(s) or not np.isfinite(sp):
        return "unclear", "A 数据缺失（预热/ATR=0）"
    if not np.isfinite(st):
        return "unclear", "A 数据缺失（强度算不出）"

    strong_dir_raw = np.isfinite(cs) and cs >= cons_min
    strong_dir = st >= strength_min and strong_dir_raw

    # 三条判定规则，逐条试
    if s > 0 and (sp > sep_min or strong_dir):
        return "bull", ""
    if s < 0 and (sp < -sep_min or strong_dir):
        return "bear", ""
    if abs(sp) < sep_min and st < strength_min:
        return "sideways", ""

    # ---- 走到这里 = unclear，归因 ----
    # B: 间距够大，但符号与斜率相反（均线排列与动能背离）
    if abs(sp) > sep_min and np.sign(sp) != np.sign(s):
        return "unclear", "B 间距符号与斜率背离"
    # C/D: 间距够（同向），但强度不够 → 卡在 strong_dir，且 sideways 也不成立
    if abs(sp) >= sep_min and st < strength_min:
        return "unclear", "C 间距够但强度不足（slow trend）"
    # 间距不够、强度够，但一致性不够
    if abs(sp) < sep_min and st >= strength_min and not strong_dir_raw:
        return "unclear", "D 强度够但一致性不足"
    # 兜底：理论上不该到这里
    return "unclear", f"E 未归类 slope={s:.2f} sep={sp:.2f} st={st:.2f} cs={cs}"


for label, (sp_, st_, cs_) in {
    "基线 sep=0.15 str=0.10 cons=0.60": (0.15, 0.10, 0.60),
    "最优 sep=0.10 str=0.05 cons=0.40": (0.10, 0.05, 0.40),
}.items():
    print("=" * 78)
    print(f"归因：{label}")
    print("=" * 78)
    reasons = Counter()
    examples = {}
    cyc = Counter()
    for d, human in TRUTH.items():
        i = IDX[d]
        c, why = diagnose(i, sp_, st_, cs_)
        cyc[c] += 1
        if c == "unclear":
            reasons[why] += 1
            examples.setdefault(why, []).append((d, human))
    total = sum(reasons.values())
    for k, v in reasons.most_common():
        print(f"  {k:<34} {v:>4} 天  ({v / total:>5.1%})")
    print(f"  unclear 合计 {total} 天")
    print(f"  周期分布 {dict(cyc)}")
    print()
    # 每类的样例 + 该类的真值分布
    for k, _ in reasons.most_common():
        ex = examples[k][:3]
        humans = Counter(h.display for _, h in examples[k])
        print(f"  [{k}] 真值分布 {dict(humans)}")
        print(f"      样例 {', '.join(f'{d}({h.display})' for d, h in ex)}")
    print()

# ---------- 一致性扫描扩到边界以下 ----------
print("=" * 78)
print("一致性扫描扩界（sep=0.10 str=0.05，unclear 进分母）")
print("=" * 78)
print(f"  {'cons':>6} {'一致率':>8} {'错向':>5} {'unclear':>8}")


def score(sep_min, strength_min, cons_min):
    ok = mis = denom = unc = 0
    for d, human in TRUTH.items():
        i = IDX[d]
        c, _ = diagnose(i, sep_min, strength_min, cons_min)
        denom += 1
        if c == "unclear":
            unc += 1
            continue
        m = LEGACY[c]
        if m == human:
            ok += 1
        if human in truth.EXTREME and m == truth.CALM:
            mis += 1
    return ok / denom if denom else 0, mis, unc, denom


for cs in [0.60, 0.50, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.10, 0.0]:
    r = score(0.10, 0.05, cs)
    print(f"  {cs:>6.2f} {r[0]:>7.1%} {r[1]:>5} {r[2]:>8}")

print()
print("=" * 78)
print("若把 B（间距与斜率背离）也当作独立可判状态")
print("=" * 78)
ok = mis = denom = 0
for d, human in TRUTH.items():
    i = IDX[d]
    c, why = diagnose(i, 0.10, 0.05, 0.40)
    if c == "unclear" and why.startswith("B"):
        # 背离时按斜率方向定周期（动能优先）
        c = "bear" if SLOPE[i] < 0 else "bull"
    denom += 1
    if c == "unclear":
        continue
    m = LEGACY[c]
    if m == human:
        ok += 1
    if human in truth.EXTREME and m == truth.CALM:
        mis += 1
print(f"  一致率 {ok / denom:.1%}  错向 {mis}  计入 {denom}")
