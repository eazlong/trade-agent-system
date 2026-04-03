---
name: technical-analysis
description: 对指定交易品种进行完整技术面分析，输出结构化分析报告
when_to_use: 用户请求分析某个交易品种（BTC、ETH、股票、外汇等）的技术面时激活
---

# 技术分析技能

## 分析流程

### 步骤 1：获取 K 线数据

使用 `ccxt` 从交易所获取 K 线数据，同时请求日线（1D）和 4 小时（4h）两个周期：

```python
import ccxt
import pandas as pd

exchange = ccxt.binance()
symbol = payload.get('symbol', 'BTC/USDT')  # 用户指定品种

# 日线 + 4小时
dfs = {}
for tf in ['1d', '4h']:
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=200)
    dfs[tf] = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    dfs[tf]['timestamp'] = pd.to_datetime(dfs[tf]['timestamp'], unit='ms')
```

支持的交易所：`binance`、`okx`、`bybit`、`coinbase` 等 ccxt 支持的交易所。
品种格式：`BTC/USDT`、`ETH/USDT`、`AAPL/NASDAQ` 等。

### 步骤 2：计算技术指标

使用 `pandas` 和 `numpy` 计算以下指标：

```python
def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def calc_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_kdj(high, low, close, n=9, m1=3, m2=3):
    lowest_low = low.rolling(window=n).min()
    highest_high = high.rolling(window=n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
    K = rsv.ewm(alpha=1/m1, adjust=False).mean()
    D = K.ewm(alpha=1/m2, adjust=False).mean()
    J = 3 * K - 2 * D
    return K, D, J
```

对每个时间周期计算并报告：
- **EMA**: EMA20、EMA50、EMA200（判断趋势方向与位置）
- **MACD**: MACD 线、Signal 线、Histogram（判断动量）
- **KDJ**: K、D、J 值（判断超买超卖）
- **其他常用指标**:
  - RSI(14): 相对强弱指数
  - ATR(14): 平均真实波幅（止损参考）
  - 布林带(20,2): 中轨 MA20，上下轨 ±2σ

### 步骤 3：识别技术形态

分析以下形态（基于价格结构和成交量）：

| 形态类型 | 具体形态 |
|----------|----------|
| 趋势反转 | 头肩底/顶、双底/双顶、V 型反转 |
| 盘整整理 | 对称三角形、上升三角形、下降三角形、旗形、楔形 |
| 持续形态 | 头肩形（持续型）、通道、矩形 |

形态识别方法：基于价格峰值/谷值的几何关系判断，无需复杂算法。

### 步骤 4：生成 K 线图表

使用 `matplotlib` 生成图表，保存为图片文件供视觉分析：

```python
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO

def plot_kline(df: pd.DataFrame, title: str, indicators: dict = None) -> BytesIO:
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(16, 10),
                                          gridspec_kw={'height_ratios': [3, 1, 1]})

    # K 线（蜡烛图）
    for idx in range(len(df)):
        open_, high, low, close = df.iloc[idx][['open', 'high', 'low', 'close']]
        color = '#26a69a' if close >= open_ else '#ef5350'
        ax1.plot([idx, idx], [low, high], color=color, linewidth=1)
        ax1.plot([idx, idx], [open_, close], color=color, linewidth=3)

    # EMA 叠加
    if indicators and 'ema20' in indicators:
        ax1.plot(df.index, indicators['ema20'], label='EMA20', color='blue', linewidth=1)
    if indicators and 'ema50' in indicators:
        ax1.plot(df.index, indicators['ema50'], label='EMA50', color='orange', linewidth=1)
    if indicators and 'ema200' in indicators:
        ax1.plot(df.index, indicators['ema200'], label='EMA200', color='purple', linewidth=1)

    ax1.set_title(title)
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # 成交量
    colors = ['#26a69a' if df.iloc[i]['close'] >= df.iloc[i]['open'] else '#ef5350'
              for i in range(len(df))]
    ax2.bar(df.index, df['volume'], color=colors, width=0.8, alpha=0.7)
    ax2.set_ylabel('Volume')

    # MACD
    if indicators and 'macd' in indicators:
        ax3.plot(df.index, indicators['macd'], label='MACD', color='blue')
        ax3.plot(df.index, indicators['signal'], label='Signal', color='orange')
        ax3.bar(df.index, indicators['histogram'],
               color=['green' if v >= 0 else 'red' for v in indicators['histogram']], alpha=0.5)
        ax3.axhline(0, color='gray', linewidth=0.5)
        ax3.legend()

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    buf.seek(0)
    plt.close()
    return buf
```

**图表输出要求**：
- 日线图表文件: `chart_1d.png`
- 4 小时图表文件: `chart_4h.png`
- 使用 vision 模型分析生成的图表，提取关键信息：
  - 趋势方向与力度
  - 支撑位与阻力位
  - 形态识别结果
  - 异常信号（如跳空、放量）

### 步骤 5：基本面搜索（如需要）

如果分析需要基本面信息，使用 `web_search` 工具搜索相关新闻：

```python
# 搜索关键词
keywords = f"{symbol} news OR fundamental OR macro outlook 2026"
```

结合搜索结果与纯技术分析，形成综合判断。

### 步骤 6：读取用户交易系统（如提供）

如果用户提供了自定义交易系统（规则文件或描述），读取并结合规则：

- 提取规则中的技术条件（如：EMA 金叉、RSI < 30 等）
- 判断当前市场状态是否满足用户规则
- 给出规则匹配度评分

### 步骤 7：输出结构化分析报告

最终输出格式：

```
## 技术分析报告: {symbol}

### 概览
- 当前价格: {price}
- 24h 成交量: {volume_24h}
- 市场状态: [多头/空头/震荡]

---

### 技术指标

| 指标 | 数值 | 信号 |
|------|------|------|
| EMA20 | {ema20} | {方向} |
| EMA50 | {ema50} | {方向} |
| EMA200 | {ema200} | {方向} |
| RSI(14) | {rsi} | {超买/超卖/中性} |
| MACD | {macd_val} | {金叉/死叉/收敛/发散} |
| KDJ | K={k}, D={d}, J={j} | {超买/超卖} |
| ATR(14) | {atr} | — |

**EMA 多空排列**: [多头排列（EMA20>EMA50>EMA200）/ 空头排列 / 混乱]
**布林带位置**: 当前价格在 [上轨/中轨/下轨] 附近

---

### 形态分析

**日线形态**: [识别到的形态]
**4小时形态**: [识别到的形态]
**综合结论**: [看多/看空/中性 + 理由]

---

### 图表视觉分析

[vision 模型对图表的分析结论]

---

### 关键价位

| 类型 | 价格 |
|------|------|
| 强阻力 | {r1} |
| 弱阻力 | {r2} |
| 强支撑 | {s1} |
| 弱支撑 | {s2} |

---

### 综合判断

**主方向**: [看多/看空/震荡]
**入场参考**:
  - 做多: 突破 {r1} 企稳，止损 {stop_loss}
  - 做空: 跌破 {s1} 确认，止损 {stop_loss}

**风险提示**: {atr} 波动率，注意止损设置
```

## 输入参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| symbol | str | 否 | BTC/USDT | 交易品种 |
| exchange | str | 否 | binance | 交易所 |
| user_strategy | str | 否 | None | 用户交易系统描述 |
| include_fundamental | bool | 否 | false | 是否搜索基本面信息 |

## 注意事项

- K 线数据 limit 不少于 200 根，确保 EMA200 计算完整
- 日线和 4 小时分析结果不一致时，以日线为主方向
- 所有价格和指标数值保留 4 位小数
- 图表使用 vision 模型分析前，确保已调用 `plt.close()` 释放内存
- 如果 ccxt 连接失败，返回错误信息并提示用户检查网络或交易所状态
