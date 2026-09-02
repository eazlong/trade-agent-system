# 指标公式与绘图代码

仅在需要具体实现时查阅。SKILL.md 中的步骤不依赖本文件的逐字代码，模型可根据指标名与参数独立实现。

## 指标实现

```python
import pandas as pd
import numpy as np

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def calc_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_kdj(high, low, close, n=9, m1=3, m2=3):
    lowest_low = low.rolling(window=n).min()
    highest_high = high.rolling(window=n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
    K = rsv.ewm(alpha=1/m1, adjust=False).mean()
    D = K.ewm(alpha=1/m2, adjust=False).mean()
    J = 3 * K - 2 * D
    return K, D, J

def calc_atr(high, low, close, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def calc_bollinger(series: pd.Series, period: int = 20, std_dev: float = 2.0):
    mid = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return mid, upper, lower
```

## K 线绘图

三张子图：主图（K 线 + EMA 叠加）、成交量、MACD。

```python
import matplotlib.pyplot as plt
from io import BytesIO

def plot_kline(df: pd.DataFrame, title: str, indicators: dict = None) -> BytesIO:
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(16, 10),
        gridspec_kw={'height_ratios': [3, 1, 1]}
    )

    # K 线
    for idx in range(len(df)):
        o, h, l, c = df.iloc[idx][['open', 'high', 'low', 'close']]
        color = '#26a69a' if c >= o else '#ef5350'
        ax1.plot([idx, idx], [l, h], color=color, linewidth=1)
        ax1.plot([idx, idx], [o, c], color=color, linewidth=3)

    # EMA 叠加
    if indicators:
        ema_cfg = [('ema20', 'blue'), ('ema50', 'orange'), ('ema200', 'purple')]
        for key, color in ema_cfg:
            if key in indicators:
                ax1.plot(df.index, indicators[key], label=key.upper(),
                         color=color, linewidth=1)

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
        hist_colors = ['green' if v >= 0 else 'red' for v in indicators['histogram']]
        ax3.bar(df.index, indicators['histogram'], color=hist_colors, alpha=0.5)
        ax3.axhline(0, color='gray', linewidth=0.5)
        ax3.legend()

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    buf.seek(0)
    plt.close()  # 释放内存
    return buf
```

**输出**：保存为 `chart_1d.png` 与 `chart_4h.png`；若环境支持 vision 模型，将图传入做视觉分析以提取趋势力度、支撑阻力、异常信号（跳空、放量）。
