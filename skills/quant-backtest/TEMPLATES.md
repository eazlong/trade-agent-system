# 回测报告模板

## 普通回测

```
## 回测报告

**策略**: {strategy_name} | **品种**: {symbol} | **周期**: {timeframe}
**时间**: {start_date} ~ {end_date} | **初始资金**: {initial_capital} USDT

| 指标 | 数值 |
|------|------|
| 总收益率 | {total_return_pct}% |
| 年化收益率 | {annualized_return_pct}% |
| 夏普比率 | {sharpe_ratio} |
| 最大回撤 | {max_drawdown_pct}% |
| 胜率 | {win_rate}% |
| 总交易次数 | {total_trades} |
| 盈亏比 | {profit_factor} |

**策略参数**: {parameters}

### 分析
[基于指标给出简要分析：收益风险比是否合理、是否存在过拟合迹象、建议的优化方向]
```

如有 benchmark 对比，加上：

```
| 基准对比 | 收益率 |
|----------|--------|
| 策略 | {total_return_pct}% |
| Buy & Hold | {benchmark.return_pct}% |
```

## 网格搜索

```
## 网格搜索结果

**最佳参数**: {best_params}（{best_score_metric} = {best_score}）

| 排名 | 参数组合 | 夏普 | 收益率 | 回撤 |
|------|----------|------|--------|------|
| 1 | {params} | {sharpe} | {return}% | {dd}% |
| 2 | ... | ... | ... | ... |

### 建议
[推荐的最佳参数、是否需要样本外验证、过拟合风险提示]
```
