from numba.core.serialize import pickle
import numpy as np
import pandas as pd
import logging
from datetime import datetime
import logging
from pytz import timezone
from scipy.signal import argrelextrema, find_peaks

def get_peaks(prices):
    """
    获取前一个价格峰值
    :param prices: 价格列表
    :return: [峰值价格,峰值index,前一个比峰值大的值的index], 如果没有则返回None
    """
    if len(prices) < 3:
        return None
    
    # 使用numpy的diff计算前后差值
    forward_diff = np.diff(prices, n=1, prepend=np.inf)
    backward_diff = np.diff(prices, n=1, append=np.inf)

    # 找到所有峰值点的索引（前后差值都为正）
    peak_indices = np.where((forward_diff > 0) & (backward_diff < 0))[0]
    peaks = prices[peak_indices]
    
    # 对于每个峰值，找到前面所有比它更大的值的索引
    higher_peaks_indices = []
    for i, peak in enumerate(peaks):
        # 找到在当前峰值之前且比它大的值的索引
        prev_higher = np.where(prices[:peak_indices[i]-1] > peak)[0]
        if prev_higher.size > 0:
            higher_peaks_indices.append(prev_higher[-1])  # 取最后一个
        else:
            higher_peaks_indices.append(None)
    higher_peaks_indices = np.array(higher_peaks_indices)
    
    # 返回所有峰值、峰值索引、以及前一个高于当前值的峰值索引
    return np.column_stack((
        peaks,
        peak_indices,
        higher_peaks_indices
    ))

def get_troughs(prices):
    """
    获取前一个价格低谷值
    :param prices: 价格列表
    :return: [低谷价格,低谷index,前一个比低谷小的值的index], 如果没有则返回None
    """
    if len(prices) < 3:
        return None
    
    # 使用numpy的diff计算前后差值
    forward_diff = np.diff(prices, n=1, prepend=np.inf)
    backward_diff = np.diff(prices, n=1, append=np.inf)

    # 找到所有低谷点的索引（前后差值都为负）
    trough_indices = np.where((forward_diff < 0) & (backward_diff > 0))[0]
    troughs = prices[trough_indices]
    
    # 对于每个低谷，找到前面所有比它更小的值的索引
    lower_troughs_indices = []
    for i, trough in enumerate(troughs):
        # 找到在当前低谷之前且比它小的值的索引
        prev_lower = np.where(prices[:trough_indices[i]-1] < trough)[0]
        if prev_lower.size > 0:
            lower_troughs_indices.append(prev_lower[-1])  # 取最后一个
        else:
            lower_troughs_indices.append(None)
    lower_troughs_indices = np.array(lower_troughs_indices)
    
    # 返回所有低谷、低谷索引、以及前一个低于当前值的低谷索引
    return np.column_stack((
        troughs,
        trough_indices,
        lower_troughs_indices
    ))

def get_previous_peak(prices, n):
    """
    获取前面n个值的最大值
    :param prices: 价格列表
    :param n: 要查找的前n个值
    :return: 前面n个值的最大值，如果n大于列表长度则返回None
    """
    if len(prices) < n:
        return None
    
    return np.max(prices[-n:])

def get_previous_trough(prices, n):
    """
    获取前面n个值的最小值
    :param prices: 价格列表
    :param n: 要查找的前n个值
    :return: 前面n个值的最小值，如果n大于列表长度则返回None
    """
    if len(prices) < n:
        return None
    
    return np.min(prices[-n:])
    
class PreviousPeakTroughFinder:
    class Info:
        def __init__(self, is_peak=True, previous_value=0):
            self.is_peak = is_peak
            self.previous_value = previous_value

    def __init__(self, period_between_peaks_or_troughs, period_of_peaks_or_troughs ):
        self.period_between_peaks_or_troughs = period_between_peaks_or_troughs
        self.period = period_of_peaks_or_troughs # 15m*4*4

    def find(self, dataframe, cur_price):
        c = cur_price
        p = np.where((dataframe['low'][:-2] <= c) & (c <= dataframe['high'][:-2]))[0]
        if p.size > 0:  
            i = p[-1]
            if dataframe['close'].size - i > self.period_between_peaks_or_troughs:
                start = i-self.period_between_peaks_or_troughs if i-self.period_between_peaks_or_troughs > 0 else 0
                hi = np.argmax(dataframe['high'][start:i+self.period_between_peaks_or_troughs]) + start
                if abs(hi - i) < self.period:
                    h = dataframe['high'].iloc[hi]
                    # logging.info(f"find previous peak {h} at {datetime.fromtimestamp(dataframe['timestamp'].iloc[i]/1000)}, cur value {c}")
                    return PreviousPeakTroughFinder.Info(True, h)
                
                li = np.argmin(dataframe['low'][start:i+self.period_between_peaks_or_troughs]) + start
                if abs(li - i) < self.period:
                    l = dataframe['low'].iloc[li]
                    # logging.info(f"find previous trough {l} at {datetime.fromtimestamp(dataframe['timestamp'].iloc[i]/1000)}, cur value {c}")
                    return PreviousPeakTroughFinder.Info(False, l)
        return None

class SupportPositionFinder:
    def __init__(self, period):
        self.period = period
        
    
    def find(self, dataframe, range=None, window = 240):
        range = range if range is not None else (dataframe['open'].iloc[-1], dataframe['close'].iloc[-1])
        pmax = np.where((dataframe['high'] >= range[0]) & (dataframe['high'] <= range[1]))[0]
        pmin = np.where((dataframe['low'] <= range[1]) & (dataframe['low'] >= range[0]))[0]

        # n = self.window  # 滑动窗口大小
        # min_ = dataframe['low'][argrelextrema(dataframe['low'].values, np.less_equal, order=n)[0]]
        # max_ = dataframe['high'][argrelextrema(dataframe['high'].values, np.greater_equal, order=n)[0]]
        # imax, _ = find_peaks(dataframe['high'].values, distance=window, prominence=0.02 * np.max(dataframe['high']), height=range[0])
        # imin, _ = find_peaks(-dataframe['low'].values, distance=window, prominence=0.02 * np.max(dataframe['low']), height=-range[1])

        # pmax = [x for x in pmax if x in imax and dataframe['open'].size - x > self.period]
        # pmin = [x for x in pmin if x in imin and dataframe['open'].size - x > self.period]
        suport, resistance = self.find2(dataframe)
        max = [x for x in pmax if dataframe['high'][x] in suport]
        min = [x for x in pmin if dataframe['low'][x] in resistance]

        if len(max) + len(min) >= 4:
            logging.warning(f'dataframe {datetime.fromtimestamp(dataframe["timestamp"].iloc[-1]/1000, tz=timezone("Asia/Shanghai"))} pickle {len(max)} {len(min)} {np.mean(np.concatenate((dataframe["high"][max],dataframe["low"][min])))} ')
            
            return True
                
        return False

    def find2(self, data, window=120):
        # 计算局部高点和低点
        data['local_high'] = data['high'].rolling(window=window, center=True).max()
        data['local_low'] = data['low'].rolling(window=window, center=True).min()
        
        # 识别支撑位（重要的低点）
        support_levels = []
        for i in range(window, len(data)-window):
            if data['low'].iloc[i] == data['local_low'].iloc[i]:
                # 检查是否是显著的低点
                if data['low'].iloc[i] < data['low'].iloc[i-window:i].min() and \
                data['low'].iloc[i] < data['low'].iloc[i+1:i+window+1].min():
                    support_levels.append(data['low'].iloc[i])
        
        # 识别压力位（重要的高点）
        resistance_levels = []
        for i in range(window, len(data)-window):
            if data['high'].iloc[i] == data['local_high'].iloc[i]:
                # 检查是否是显著的高点
                if data['high'].iloc[i] > data['high'].iloc[i-window:i].max() and \
                data['high'].iloc[i] > data['high'].iloc[i+1:i+window+1].max():
                    resistance_levels.append(data['high'].iloc[i])

        return support_levels, resistance_levels
    
def trend_changed(dataframe):
    if dataframe.size < 200:
        return None, False
    
    sma20 = dataframe.ta.sma(length=20)
    sma50 = dataframe.ta.sma(length=50)
    sma100 = dataframe.ta.sma(length=100)

    current_trend = 0
    if sma20.iloc[-1] > sma50.iloc[-1] and sma50.iloc[-1] > sma100.iloc[-1]:
        current_trend = 1
        if sma50.iloc[-2] <= sma100.iloc[-2] or sma20.iloc[-2] <= sma50.iloc[-2]:
            return current_trend, True
    elif sma20.iloc[-1] < sma50.iloc[-1] and sma50.iloc[-1] < sma100.iloc[-1]:
        current_trend = -1
        if sma50.iloc[-2] >= sma100.iloc[-2] or sma20.iloc[-2] >= sma50.iloc[-2]:
            return current_trend, True

    return current_trend, False

def current_trend(dataframe):
    if dataframe.size < 200:
        return None
    
    sma20 = dataframe.ta.sma(length=20)
    sma50 = dataframe.ta.sma(length=50)
    sma200 = dataframe.ta.sma(length=100)  

    if sma20.iloc[-1] > sma50.iloc[-1] and sma50.iloc[-1] > sma200.iloc[-1]:
        return 1
    elif sma20.iloc[-1] < sma50.iloc[-1] and sma50.iloc[-1] < sma200.iloc[-1]:
        return -1

    return 0
    
from scipy.sparse import data
from scipy.stats import linregress
from sklearn.preprocessing import StandardScaler
import numpy as np

def standardized_slope(x, y):
    x = np.asarray(x).reshape(-1, 1)
    y = np.asarray(y).reshape(-1, 1)
    
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x).flatten()
    y_scaled = scaler.fit_transform(y).flatten()

    slope, _, _, r_value, _ = linregress(x_scaled, y_scaled)
    return slope, r_value