import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt

def find_support_resistance_high_low(data, window=20):
    """
    通过寻找局部高点和低点来识别支撑压力位
    """
    # 计算局部高点和低点
    data['local_high'] = data['High'].rolling(window=window, center=True).max()
    data['local_low'] = data['Low'].rolling(window=window, center=True).min()
    
    # 识别支撑位（重要的低点）
    support_levels = []
    for i in range(window, len(data)-window):
        if data['Low'].iloc[i] == data['local_low'].iloc[i]:
            # 检查是否是显著的低点
            if data['Low'].iloc[i] < data['Low'].iloc[i-window:i].min() and \
               data['Low'].iloc[i] < data['Low'].iloc[i+1:i+window+1].min():
                support_levels.append(data['Low'].iloc[i])
    
    # 识别压力位（重要的高点）
    resistance_levels = []
    for i in range(window, len(data)-window):
        if data['High'].iloc[i] == data['local_high'].iloc[i]:
            # 检查是否是显著的高点
            if data['High'].iloc[i] > data['High'].iloc[i-window:i].max() and \
               data['High'].iloc[i] > data['High'].iloc[i+1:i+window+1].max():
                resistance_levels.append(data['High'].iloc[i])
    
    return support_levels, resistance_levels

from sklearn.cluster import KMeans

def find_support_resistance_clustering(data, n_clusters=8):
    """
    使用K-means聚类识别支撑压力位
    """
    # 使用最高价、最低价和收盘价作为特征
    price_data = data[['High', 'Low', 'Close']].values.flatten().reshape(-1, 1)
    
    # 使用K-means聚类
    kmeans = KMeans(n_clusters=n_clusters, random_state=42)
    kmeans.fit(price_data)
    
    # 获取聚类中心并排序
    levels = sorted(kmeans.cluster_centers_.flatten())
    
    # 分离支撑位和压力位
    current_price = data['Close'].iloc[-1]
    support_levels = [level for level in levels if level < current_price]
    resistance_levels = [level for level in levels if level > current_price]
    
    return support_levels, resistance_levels

def find_support_resistance_ma(data, periods=[5, 10, 20, 50, 100, 200]):
    """
    通过移动平均线识别动态支撑压力位
    """
    support_levels = []
    resistance_levels = []
    
    for period in periods:
        ma = data['Close'].rolling(window=period).mean()
        current_ma = ma.iloc[-1]
        current_price = data['Close'].iloc[-1]
        
        if current_ma < current_price:
            support_levels.append(current_ma)
        else:
            resistance_levels.append(current_ma)
    
    return support_levels, resistance_levels


def find_fibonacci_levels(high, low):
    """
    计算斐波那契回撤位作为支撑压力位
    """
    diff = high - low
    levels = {
        '0.0%': high,
        '23.6%': high - 0.236 * diff,
        '38.2%': high - 0.382 * diff,
        '50.0%': high - 0.5 * diff,
        '61.8%': high - 0.618 * diff,
        '78.6%': high - 0.786 * diff,
        '100.0%': low
    }
    return levels       


def analyze_support_resistance(symbol, period="6mo"):
    """
    完整的支撑压力位分析函数
    """
    # 获取数据
    stock = yf.download(symbol, period=period)
    
    if stock.empty:
        print(f"无法获取 {symbol} 的数据")
        return
    
    # 方法1: 高点和低点
    support1, resistance1 = find_support_resistance_high_low(stock)
    
    # 方法2: 聚类分析
    support2, resistance2 = find_support_resistance_clustering(stock)
    
    # 方法3: 移动平均线
    support3, resistance3 = find_support_resistance_ma(stock)
    
    # 方法4: 斐波那契
    recent_high = stock['High'].max()
    recent_low = stock['Low'].min()
    fib_levels = find_fibonacci_levels(recent_high, recent_low)
    
    # 合并结果
    all_support = list(set(support1[-5:] + support2 + support3))
    all_resistance = list(set(resistance1[-5:] + resistance2 + resistance3))
    
    # 排序并选择最重要的几个
    current_price = stock['Close'].iloc[-1]
    important_support = sorted([s for s in all_support if s < current_price], reverse=True)[:3]
    important_resistance = sorted([r for r in all_resistance if r > current_price])[:3]
    
    print(f"\n{symbol} 支撑压力位分析:")
    print(f"当前价格: {current_price:.2f}")
    print(f"主要支撑位: {[f'{x:.2f}' for x in important_support]}")
    print(f"主要压力位: {[f'{x:.2f}' for x in important_resistance]}")
    print(f"斐波那契关键位:")
    for level, price in fib_levels.items():
        print(f"  {level}: {price:.2f}")
    
    # 可视化
    plt.figure(figsize=(12, 8))
    plt.plot(stock.index, stock['Close'], label='收盘价', linewidth=1)
    
    # 画支撑线
    for level in important_support:
        plt.axhline(y=level, color='green', linestyle='--', alpha=0.7, label=f'支撑 {level:.2f}')
    
    # 画压力线
    for level in important_resistance:
        plt.axhline(y=level, color='red', linestyle='--', alpha=0.7, label=f'压力 {level:.2f}')
    
    plt.title(f'{symbol} 支撑压力位分析')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.show()
    
    return important_support, important_resistance

# 使用示例
if __name__ == "__main__":
    # 分析苹果股票
    support, resistance = analyze_support_resistance("AAPL")


def find_volume_weighted_levels(data, window=10):
    """
    基于成交量加重的价格区域识别支撑压力位
    """
    # 计算价格区间
    data['price_zone'] = (data['Close'] / window).round() * window
    
    # 计算每个价格区间的成交量总和
    volume_by_zone = data.groupby('price_zone')['Volume'].sum()
    
    # 找出成交量密集的区域
    high_volume_zones = volume_by_zone.nlargest(5)
    
    current_price = data['Close'].iloc[-1]
    support_levels = []
    resistance_levels = []
    
    for zone, volume in high_volume_zones.items():
        if zone < current_price:
            support_levels.append(zone)
        else:
            resistance_levels.append(zone)
    
    return support_levels, resistance_levels