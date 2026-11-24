# Box Range Detection Tool

新增 `DetectBoxRangeTool`：判断指定 K 线窗口是否处于箱体震荡，并返回上下边界。纯计算函数放 `apps/strategy_engine/indicators.py`，工具壳放 `apps/agent/tools/box_range.py`，与 `GetHorizontalKeyLevelsTool` 的两层拆分完全对齐。

**关键 trade-off**：

- **边界聚合复用** `horizontal_key_levels` 的 cluster 算法（`indicators.py:256` 的聚类循环），不重新实现。代价是引入了 tolerance_pct 概念（与 `horizontal_key_levels` 共用 `_latest_tolerance_pct`）；收益是工具间算法一致、单元测试可借现有覆盖。
- **"两次验证"取解读 3（双重验证）**：上下边界同时满足 `pivot_count ≥ min_pivots` 且 `touches ≥ min_touches`。拒绝单一来源验证（容易被单根异常 K 线误导）和单纯触碰验证（可能只是巧合）。
- **宽度阈值只支持 `max_width_abs` / `max_width_pct` 二选一**：拒绝 ATR 倍数方案。ATR 倍数会引入 `atr_period` 与 `max_width_atr` 两个新参数，工具边界变宽，违背"最小够用"。

**拒绝的方案**：

- 边界用分位数（quantile）——上下边界来源不对称
- 边界用 Donchian Channel ——只看末段，不符合"整窗口震荡"语义
- 宽度用 ATR 倍数 ——见上
- 工具内置 `start_time/end_time` 过滤 —— 时间窗口是策略选择，不是技术指标职责
