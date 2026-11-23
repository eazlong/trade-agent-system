"""诊断 detect_box_range 为何零检出（独立脚本，只读）。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app")
os.chdir("/app")

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.prod")
django.setup()

from apps.regime import config  # noqa: E402
from apps.regime.models import DailyCandle  # noqa: E402
from apps.strategy_engine.indicators import detect_box_range  # noqa: E402

SYMBOL = config.CANDLES.symbol

rows = list(
    DailyCandle.objects.filter(symbol=SYMBOL)
    .order_by("date")
    .values("date", "open", "high", "low", "close", "volume")
)
print(f"日线 {len(rows)} 根")

BOX = config.BOX.kwargs()
print(f"BOX 参数: {BOX}")
print()

# 用 2024-04-19 ~ 2024-09-30 那段真值箱体（人工明确标为横盘）
seg = [
    r for r in rows if str(r["date"]) >= "2024-04-01" and str(r["date"]) <= "2024-10-05"
]
print(f"测试段：人工标注为箱体的 2024 年 4-9 月，共 {len(seg)} 根")
print(f"  实际区间: {seg[0]['date']} ~ {seg[-1]['date']}")
closes = [float(r["close"]) for r in seg]
print(
    f"  close 范围: {min(closes):.0f} ~ {max(closes):.0f}  宽度 {(max(closes) / min(closes) - 1):.1%}"
)

klines = [{"high": r["high"], "low": r["low"], "close": r["close"]} for r in seg]
res = detect_box_range(klines, **BOX)
print()
print("detect_box_range 返回：")
for k, v in res.items():
    print(f"  {k}: {v}")
print()

# 逐项检查门槛
print("门槛检查：")
print(f"  pivot_window={BOX.get('pivot_window')}  min_pivots={BOX.get('min_pivots')}")
print(f"  min_touches={BOX.get('min_touches')}  min_gap_bars={BOX.get('min_gap_bars')}")
print(f"  min_duration_bars={BOX.get('min_duration_bars')}")
print(
    f"  max_width_pct={BOX.get('max_width_pct')}  min_width_pct={BOX.get('min_width_pct')}"
)
print(f"  窗口根数 {len(klines)}")
print()

# 放宽参数再试
print("放宽参数重试：")
for label, kwargs in [
    ("默认", {}),
    ("去掉时长门槛", {"min_duration_bars": None}),
    ("放宽宽度到 50%", {"max_width_pct": 0.50}),
    ("只留 1 个 pivot/1 次触碰", {"min_pivots": 1, "min_touches": 1}),
    (
        "全放宽",
        {
            "min_pivots": 1,
            "min_touches": 1,
            "min_duration_bars": None,
            "max_width_pct": 0.60,
            "min_width_pct": 0.001,
        },
    ),
]:
    kw = dict(BOX)
    kw.update(kwargs)
    try:
        r = detect_box_range(klines, **kw)
        ok = r.get("upper") is not None and r.get("lower") is not None
        print(f"  {label:<24} 检出={ok}  reason={r.get('reason', '')[:60]}")
    except Exception as e:
        print(f"  {label:<24} 异常: {e}")
