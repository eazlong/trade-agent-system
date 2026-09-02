# 策略参数示例

## 策略参数（parameters 字段）

```json
// EMA 交叉策略
{"ma_fast": 10, "ma_slow": 30}

// RSI 策略
{"rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30}

// 布林带 + RSI
{"bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_oversold": 30}

// MACD + ATR
{"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "atr_period": 14}

// 均线交叉 + 成交量
{"fast_period": 5, "slow_period": 20, "volume_multiplier": 1.5}
```

具体参数名取决于策略代码中 `params_schema` 的定义，可用 `read_file` 查看策略源文件获取。

## 网格搜索配置

用户要求参数优化时，添加 `grid_search` 对象：

```json
{
  "enabled": true,
  "parameters": {
    "rsi_period": {"min": 10, "max": 30, "step": 5},
    "rsi_threshold": {"min": 20, "max": 40, "step": 5}
  },
  "sort_by": "sharpe_ratio",
  "max_combinations": 100
}
```

`sort_by` 可选：`sharpe_ratio`、`total_return_pct`、`win_rate`。
