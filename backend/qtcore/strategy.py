from collections import namedtuple
from datetime import datetime, timezone, tzinfo
import logging
import pandas as pd
from utils.redis_cache import RedisDict
from qtcore.binance_helper import CommonBinanceHelper
import pandas_ta as ta
import numpy as np

from enum import Enum

from abc import ABC, abstractmethod

from .i_wss_data_consumer import WSSDataConsumer

from utils.metrics import PreviousPeakTroughFinder, trend_changed, SupportPositionFinder

import pytz
tz = pytz.timezone('Asia/Shanghai')

class Direct(Enum):
    NONE = 0
    LONG = 1
    SHORT = 2

class StrategySignal:
    def __init__(self, direct, params={}, symbol="", strategy="", interval="",  data={}, msg=""):
        self.symbol = symbol
        self.strategy = strategy
        self.interval = interval
        self.direct = direct
        self.data = data
        self.msg = msg
        self.params = params

class StrategySignalProcessor(ABC):
    def __init__(self, name):
        self.name = name

    @abstractmethod
    def do(self, signal):
        pass

    @abstractmethod
    def destroy(self):
        pass
 
class Strategy(WSSDataConsumer):
    def __init__(self, name, interval, symbols): 
        super().__init__(name, interval, symbols)

        self.listener = []
        self.template = self._load_template()

    def _load_template(self):
        if self.name:
            from strategy.models import TbStrategyTemplate
            return TbStrategyTemplate.objects.get(name=self.name)
        
    def need_process(self, interval, symbol):
        if interval in self.interval:
            if len(self.symbols) != 0 and  symbol in self.symbols:
                return True
            elif len(self.symbols) == 0:
                return True
        return False
    
    @abstractmethod
    def analyse_data(self, symbol, data, interval):
        pass    
    
    def format_msg(self, signal):
        msg = self.template.content.replace("{symbol}", signal.symbol)
        msg = msg.replace("{{direction}}", "上涨" if signal.direct == Direct.LONG else "下跌" )
        return msg

    def process(self, symbol, data, interval):
        try:
            signal = self.analyse_data(symbol, data, interval)
            if signal.direct != Direct.NONE:
                signal.symbol = symbol
                signal.strategy = self.name
                signal.interval = interval
                signal.data = data
                signal.template = self.template
                signal.msg = self.format_msg(signal)
                for listener in self.listener:
                    listener['processor'].do(signal)
        except Exception as e:
            logging.exception(e)

    def register(self, listener, processor):
        logging.info(f"{self.name} register listen user {listener} for {processor}")
        self.listener.append({'listener':listener, 'processor': processor})

    def unregister(self, listener, name):
        logging.info(f"{self.name} unregister listen user {listener} - {name} ")
        for item in self.listener:
            if item['listener'] == listener and item['processor'].name == name:
                self.listener.remove(item)
                item['processor'].destroy()
                break

    def get_register(self, listener, name):
        for item in self.listener:
            if item['listener'] == listener and item['processor'].name == name:
                return item['processor']
            
class TrendAnalysisStrategy(Strategy):
    def __init__(self):
        super().__init__(self.__class__.__name__.replace("Strategy", ""), ['15m', '1h', '4h', '1d'], [])
        self.status = {}
    
    #这个策略有点特殊，None也要处理
    def process(self, symbol, data, interval):
        try:
            signal = self.analyse_data(symbol, data, interval)
            signal.symbol = symbol
            signal.strategy = self.name
            signal.interval = interval
            signal.data = data
            signal.template = self.template
            signal.msg = self.format_msg(signal)
            for listener in self.listener:
                listener['processor'].do(signal)
        except Exception as e:
            logging.exception(e)

    def analyse_data(self, symbol, data, interval):
        df = pd.DataFrame.from_dict(data)
        df['dt'] = pd.to_datetime(df['timestamp'], unit='ms')

        trend, changed = trend_changed(df)
        return StrategySignal(Direct.LONG, params={'trend': trend, 'changed': changed}) if trend == 1 else StrategySignal(Direct.SHORT, params={'trend': trend, 'changed': changed}) if trend == -1 else StrategySignal(Direct.NONE, params={'trend': trend, 'changed': changed})

class BigWave2Strategy(Strategy):
    K_LINE_COUNT = 3
    RISE_PERSENT = 0.03
    DOWN_PERSENT = -0.10

    def __init__(self):
        super().__init__(self.__class__.__name__.replace("Strategy", ""), ['1m'], [])
        self.status = {}

    def analyse_data(self, symbol, data, interval):
        if len(data['timestamp']) < self.K_LINE_COUNT:
            return StrategySignal(Direct.NONE)
        
        c = data['close'][-1] - data['close'][-self.K_LINE_COUNT]
        rise = c/data['close'][-self.K_LINE_COUNT]
        if rise >= self.RISE_PERSENT:
            if symbol in self.status:
                trigger_time = self.status[symbol]
                if data['timestamp'][-1] - trigger_time <= 5*60*1000:
                    logging.debug(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}: 第二次上涨 {rise*100:.2f}%， 间隔 {data['timestamp'][-1] - trigger_time}")
                    return StrategySignal(Direct.NONE)
                if data['timestamp'][-1] - trigger_time < 1000*60*60*12:
                    del self.status[symbol]
                    logging.debug(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}: 第三次上涨 {rise*100:.2f}%， 间隔 {data['timestamp'][-1] - trigger_time}")
                    return StrategySignal(Direct.LONG, params={'rise': rise}, symbol=symbol, strategy=self.name, interval=interval, data=data)
                else:
                    logging.debug(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}: 第一次上涨 {rise*100:.2f}% 间隔太长，重新开始")
                    self.status[symbol] = data['timestamp'][-1]
            else:
                logging.debug(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}: 第一次上涨 {rise*100:.2f}%")
                self.status[symbol] = data['timestamp'][-1]
        elif rise <= self.DOWN_PERSENT:
            if symbol in self.status:
                del self.status[symbol]
            return StrategySignal(Direct.SHORT, params={'rise': rise}, symbol=symbol, strategy=self.name, interval=interval, data=data)
        
        return StrategySignal(Direct.NONE)

    def format_msg(self, signal):
        msg = self.template.content.replace("{symbol}", signal.symbol)
        msg = msg.replace("{{interval}}", f"{self.K_LINE_COUNT}m")
        msg = msg.replace("{{direction}}", "上涨" if signal.direct == Direct.LONG else "下跌" )
        msg = msg.replace("{{rise}}", f"{abs(signal.params['rise'])*100:.2f}%")
        return msg
    
class BigWaveStrategy(Strategy):
    K_LINE_COUNT = 16*60
    VOL_MULT = 1.5
    RISE_MULT = 3
    TRIGGER_INTERVAL = 4*60
    PRICE_RISE_THRESHOLD = 0.008

    # 放量上涨检测参数
    VOLUME_SURGE_MULT = 3.0  # 成交量倍数阈值
    PRICE_SURGE_THRESHOLD = 0.01 # 价格上涨阈值
    SURGE_DETECTION_WINDOW = 480  # 检测窗口大小（K线数量）
    SURGE_COUNT = 3

    def __init__(self, name=None):
        if not name:
            name = self.__class__.__name__.replace("Strategy", "")
        # logging.info(f"name {name} init")
        super().__init__(name, ['1m', '15m'], [])
        self.surge_history = {}  # 记录各symbol的放量上涨历史
        self.support_position_finder = SupportPositionFinder(self.TRIGGER_INTERVAL)

    def _detect_previous_volume_surge(self, symbol, data):
        if len(data['timestamp']) < self.SURGE_DETECTION_WINDOW:
            return False
        
        if symbol not in self.surge_history:
            self.surge_history[symbol] = { 'rise': [], 'fall': [] }
        
            for i in range(len(data['timestamp']) - self.SURGE_DETECTION_WINDOW, len(data['timestamp'])-1):
                self._is_volume_surge(symbol, {'timestamp': data['timestamp'][0:i], 'open': data['open'][0:i], 'high': data['high'][0:i], 'low': data['low'][0:i], 'close': data['close'][0:i], 'volume': data['volume'][0:i]})
        else:
            self._is_volume_surge(symbol, data)
        return True

    def _is_volume_surge(self, symbol, data):
        """检测前面行情中是否出现过放量上涨"""
        if len(data['timestamp']) < self.SURGE_DETECTION_WINDOW:
            return False
        
        self.surge_history[symbol]['rise'] = [s for s in self.surge_history[symbol]['rise'] if data['low'][-1] > s['low'] and data['timestamp'][-1] - s['timestamp'] < 24*60*60*1000]
        self.surge_history[symbol]['fall'] = [s for s in self.surge_history[symbol]['fall'] if data['high'][-1] < s['high'] and data['timestamp'][-1] - s['timestamp'] < 24*60*60*1000]
        sr = self.surge_history[symbol]['rise']
        sf = self.surge_history[symbol]['fall']
            
        # 检查历史数据中的放量上涨情况
        price_rise = (data['close'][-1] - data['open'][-1]) / data['open'][-1]
        key = 'rise' if price_rise > 0 else 'fall'

        if abs(price_rise) >= self.PRICE_SURGE_THRESHOLD or symbol == 'BTCUSDT':
            if key == 'rise':
                pre_index = -1 if len(sr) == 0 else data['timestamp'].index(sr[-1]['timestamp'])
                l = 48 if len(sr) == 0 or pre_index == -1 else len(data['timestamp']) - pre_index
                if l < 12:
                    return False

                if data['close'][-1] > np.max(data['high'][-l:-1]) and data['open'][-1] < np.max(data['high'][-l:-1]):
                    surge_info = {
                        'timestamp': data['timestamp'][-1],
                        'price_rise': price_rise,
                        'open': data['open'][-1],
                        'close': data['close'][-1],
                        'low': min(min(data['close'][-l:-1]), min(data['open'][-l:-1])),
                        'high': max(max(data['close'][-l:-1]), max(data['open'][-l:-1]))
                    }
                    self.surge_history[symbol]['rise'].append(surge_info)
                    logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000, tz=tz)} {symbol}: ---- 检测放量上涨 {price_rise} {pre_index} {l} {len(self.surge_history[symbol]['rise'])} {len(sr)}")
            else:
                pre_index = -1 if len(sf) == 0 else data['timestamp'].index(sf[-1]['timestamp'])
                l = 48 if pre_index == -1 else len(data['timestamp']) - pre_index

                if l < 12:
                    return False

                if data['close'][-1] < np.min(data['low'][-l:-1]) and data['open'][-1] > np.min(data['low'][-l:-1]):
                    surge_info = {
                        'timestamp': data['timestamp'][-1],  
                        'price_rise': price_rise,
                        'open': data['open'][-1],
                        'close': data['close'][-1],
                        'low': min(min(data['close'][-l:-1]), min(data['open'][-l:-1])),
                        'high': max(max(data['close'][-l:-1]), max(data['open'][-l:-1]))
                    }
                    self.surge_history[symbol]['fall'].append(surge_info)
                # logging.debug(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}: ---- 检测放量下跌 {price_rise} {pre_index} {l} {len(self.surge_history[symbol]['fall'])} {len(sf)}")


        return True

    def _has_recent_volume_surge(self, symbol, current_timestamp, open, lookback_minutes=60*24*7, direct=Direct.NONE):
        """检查最近是否有放量上涨发生"""
        if symbol not in self.surge_history:
            return False

        lookback_ms = lookback_minutes * 60 * 1000
        key = 'rise' if direct == Direct.LONG else 'fall'
        recent_surges = [s for s in self.surge_history.get(symbol, {}).get(key, [])
                        if current_timestamp - s['timestamp'] <= lookback_ms]
        if len(recent_surges) == 0:
            return False
            
        logging.info(f"{datetime.fromtimestamp(current_timestamp/1000, tz=tz)} {symbol}: 最近有 {len(recent_surges)} 次放量 { '上涨' if direct == Direct.LONG else '下跌' }发生 {datetime.fromtimestamp(recent_surges[-1]['timestamp']/1000)} {recent_surges[-1]['price_rise']}")
        if len(recent_surges) > 0 and len(recent_surges) < 4:
            if direct == Direct.LONG and open > recent_surges[-1]['open']:
                return True
            if direct == Direct.SHORT and open < recent_surges[-1]['open']:
                return True
                
        return False

    def analyse_data(self, symbol, data, interval):
        if interval == '15m':
            # 检测前面行情中是否出现过放量上涨
            self._detect_previous_volume_surge(symbol, data)
            d = pd.DataFrame.from_dict(data)
            self.support_position_finder.find2(d)
            return StrategySignal(Direct.NONE)

        if len(data['timestamp']) <= self.K_LINE_COUNT:
            return StrategySignal(Direct.NONE)

        # 计算之前K线成交量均值
        prev_vol = np.mean(data['volume'][-60:-2])
        # 判断成交量是否大幅上升
        if data['volume'][-1] < prev_vol * self.VOL_MULT:  # 成交量翻倍
            return StrategySignal(Direct.NONE)

        df = pd.DataFrame.from_dict(data)
        df['dt'] = pd.to_datetime(df['timestamp'], unit='ms')
        maxind = np.argmax(data['high'][-self.K_LINE_COUNT:-2])
        max = data['high'][-self.K_LINE_COUNT:-2][maxind]
        minind = np.argmin(data['low'][-self.K_LINE_COUNT:-2])
        min = data['low'][-self.K_LINE_COUNT:-2][minind]

        # rise = (df['close'].iloc[-1] - df['open'].iloc[-1])/df['open'].iloc[-1]
        # logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}: max {maxind} {max}, min {minind} {min}")
        # if df['close'].iloc[-1] > max and df['open'].iloc[-1] <= max:
        if df['close'].iloc[-1] > df['open'].iloc[-1]:
            if len(self.surge_history.get('BTCUSDT', {}).get('rise', [])) == 0:
                return StrategySignal(Direct.NONE)
            
            logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000, tz=tz)} {symbol}放量突破前高: max {max} surge {len(self.surge_history[symbol]['rise']) if symbol in self.surge_history else 0 }")
            if not self.support_position_finder.find(df):
                logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000, tz=tz)} {symbol} 支撑位检测失败")
                return StrategySignal(Direct.NONE)

            #是否有长引线
            if df['high'].iloc[-1] - df['close'].iloc[-1] > (df['close'].iloc[-1] - df['open'].iloc[-1]) * 0.75:
                logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000, tz=tz)} {symbol} 长引线")
                return StrategySignal(Direct.NONE)
                
            rise = (df['close'].iloc[-1] - df['open'].iloc[-1])/df['open'].iloc[-1]
            rise_mean = np.mean((df['close'][-self.K_LINE_COUNT:-2] - df['open'][-self.K_LINE_COUNT:-2])/df['open'][-self.K_LINE_COUNT:-2])
            if rise <= rise_mean * self.RISE_MULT and rise <= self.PRICE_RISE_THRESHOLD:
                logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000, tz=tz)} {symbol} 涨幅不满足")
                return StrategySignal(Direct.NONE)
            # 检查前面行情中是否已经出现过放量上涨
            has_previous_surge = self._has_recent_volume_surge(symbol, data['timestamp'][-1], data['open'][-1], direct=Direct.LONG)
            if not has_previous_surge:
                logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000, tz=tz)} {symbol} 没有放量上涨")
                return StrategySignal(Direct.NONE)
        
            return StrategySignal(Direct.LONG, params={'rise': rise, 'pre': max, 'sl': data['low'][-1]*0.999})

        if df['close'].iloc[-1] < min and df['open'].iloc[-1] >= min:
            return StrategySignal(Direct.NONE)

            if len(self.surge_history.get('BTCUSDT', {}).get('fall', [])) == 0:
                return StrategySignal(Direct.NONE)
            
            #是否有长引线
            if df['close'].iloc[-1] - df['low'].iloc[-1] > df['open'].iloc[-1] - df['close'].iloc[-1]:
                return StrategySignal(Direct.NONE)
            rise = (df['open'].iloc[-1] - df['close'].iloc[-1])/df['open'].iloc[-1]
            rise_mean = np.mean((df['close'][-self.K_LINE_COUNT:-2] - df['open'][-self.K_LINE_COUNT:-2])/df['open'][-self.K_LINE_COUNT:-2])
            if rise <= rise_mean * self.RISE_MULT:
                logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol} 涨幅不满足")
                return StrategySignal(Direct.NONE)
            # 检查前面行情中是否已经出现过放量上涨
            has_previous_surge = self._has_recent_volume_surge(symbol, data['timestamp'][-1], data['open'][-1], direct=Direct.SHORT)
            if not has_previous_surge:
                return StrategySignal(Direct.NONE)
            return StrategySignal(Direct.SHORT, params={'rise': rise, 'pre': min, 'sl': data['high'][-1]*1.001})
        
        return StrategySignal(Direct.NONE)

    def format_msg(self, signal):
        msg = self.template.content.replace("{symbol}", signal.symbol)
        msg = msg.replace("{{interval}}", f"{self.K_LINE_COUNT}m")
        msg = msg.replace("{{direction}}", "上涨" if signal.direct == Direct.LONG else "下跌" )
        msg = msg.replace("{{rise}}", f"{abs(signal.params['rise'])*100:.2f}%")
        return msg
    
class BigWaveAfterBTCStrategy(BigWaveStrategy):
    def __init__(self):
        super().__init__(self.__class__.__name__.replace("Strategy", ""))
        self.btc_trend = False

    def analyse_data(self, symbol, data, interval):
        if symbol == 'BTCUSDT':
            if (data['close'][-1] - data['close'][-60])/data['close'][-60] > 0.01 and (data['close'][-1] - data['close'][-15])/data['close'][-15] > 0.003:
                self.btc_trend = True
            else:
                self.btc_trend = False
        
        if not self.btc_trend:
            return StrategySignal(Direct.NONE)
        
        return super().analyse_data(symbol, data, interval)
    
class FirstWaveStrategy(Strategy):
    BOTTOM_PULLBACK_DURATION = 3*24*60*60*1000 #4d
    BOTTOM_RANGE = 0.618
    SURGE_DURATION = 12*60*60*1000 

    def __init__(self):
        super().__init__(self.__class__.__name__.replace("Strategy", ""), ['1m','15m'], [])
        self.status = {}
        self.interval_in_bars = self._get_interval_in_ms()
        self.data_15m = {}
        self.status = {}

    def _get_interval_in_ms(self):
        return int(self.interval[1].replace("m", ""))*60*1000
    
    def analyse_data(self, symbol, data, interval):
        if interval == '15m':
            self.data_15m[symbol] = pd.DataFrame.from_dict(data)
            return StrategySignal(Direct.NONE)
        
        if symbol not in self.data_15m:
            return StrategySignal(Direct.NONE)

        df = self.data_15m[symbol]
        if df.size == 0:
            return  StrategySignal(Direct.NONE)
        
        if symbol in self.status:
            trigger_time = self.status[symbol]
            if data['timestamp'][-1] - trigger_time <= 30*60*1000:
                return StrategySignal(Direct.NONE)
            
        p = int(self.BOTTOM_PULLBACK_DURATION / self.interval_in_bars)
        sd = int(self.SURGE_DURATION / self.interval_in_bars)
        l = df['close'].size
        
        if l - 1 < sd + p:
            return StrategySignal(Direct.NONE)
        
        # vol = df['volume'].ewm(span=8, adjust=False).mean()
        # x = np.arange(5)
        # y = vol.iloc[-5:]
        # slope, _, _, _, _ = linregress(x, y)
        # slope = slope / np.mean(y) if np.mean(y) > 0 else 0

        # 计算最近K线成交量均值
        recent_vol = np.mean(data['volume'][len(data['volume'])-5:len(data['volume'])])
        # 计算之前K线成交量均值
        prev_vol = np.mean(data['volume'][len(data['volume'])-2*5:len(data['volume'])-5])

        c = data['close'][-1]
        mi = np.argmax(df['high'][-(p+sd):l-4])
        mi = l-(p+sd)+mi
        m = df['high'].iloc[mi]

        if l-mi>4*4 and c >= m and recent_vol > prev_vol * 3: 
            lowest = np.min(df['low'][-(p+sd):])
            price_change = (m - lowest) / lowest

            # 底部波动检测
            if abs(price_change) <= self.BOTTOM_RANGE:
                logging.info(f"{datetime.fromtimestamp(df['timestamp'].iloc[-1]/1000)} {symbol}: 底部启动检测完成 {price_change*100:.2f}% {m}")
                self.status[symbol] = df['timestamp'].iloc[-1]
                return StrategySignal(Direct.LONG, {'price_change': price_change, 'peak': m})
        
        return StrategySignal(Direct.NONE)    

class SecondWaveStrategy(Strategy):
    BOTTOM_PULLBACK_DURATION = 4*24*60*60*1000 #4d
    BOTTOM_RANGE = 0.30

    SHORT_SURGE = 0.2
    SHORT_SURGE_DURATION = 8*60*60*1000 #24*15 6小时拉升超过10%

    PULLBACK_RANGE = 0.382
    PULLBACK_DURATION = 4*60*60*1000

    RESISTANCE_BREAK_DURATION = 6*60*60*1000

    class Status:
        def __init__(self):
            self.bottom_detected = False
            self.bottom_detected_time = 0
            self.previous_peak = 0.0
            self.surge_detected = False
            self.surge_detected_pos = 0
            self.pullback_detected = False
            self.pullback_detected_pos = 0
            self.resistance_level = None
            self.crossover = False

    def __init__(self):
        self.status = RedisDict(f'{self.__class__.__name__}', {})
        super().__init__(self.__class__.__name__.replace("Strategy", ""), ['15m'], [])
        self.first = FirstWaveStrategy()

    def _get_interval_in_ms(self):
        return int(self.interval[0].replace("m", ""))*60*1000
    
    def _log(self, msg):
        logging.info(msg)

    def analyse_data(self, symbol, data, interval):
        try:
            l = len(data['close'])
            # logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}:开始检测")
            if symbol in self.status:
                s = self.status[symbol]
                if not s.bottom_detected:
                    r = self.first.analyse_data(symbol, data, interval)
                    if r.direct == Direct.LONG:                        
                        s.bottom_detected = True
                        s.bottom_detected_time = data['timestamp'][-1]
                        s.previous_peak = r.params['peak']
                        self.status[symbol] = s
                elif not s.pullback_detected:
                    #一小时内还在上涨，一浪还末结束，调整一浪结束点
                    if data['timestamp'][-1] - s.bottom_detected_time < 60*60*1000 and data['close'][-1] > max(data['close'][l-4-1:l-2]):
                        s.bottom_detected_time = data['timestamp'][-1]
                        self.status[symbol] = s
                        logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}:继续拉升")
                        return StrategySignal(Direct.NONE)
                    
                    self._detect_pullback(symbol, data)
                elif s.pullback_detected and not s.crossover:
                    self._detect_resistance_break(symbol, data)
                else:
                    if self._check_trend_reversal(symbol, data):
                        del(self.status[symbol])
                        return StrategySignal(Direct.LONG)
            else:
                self.status[symbol] = self.Status()
        except Exception as e:
            logging.exception(e)
        
        return StrategySignal(Direct.NONE)

    def _detect_pullback(self, symbol, data):
        """检测回调和盘整"""
        s = self.status[symbol]
        start = s.bottom_detected_time
        l = data['timestamp'][-1] - start
        if l < self.PULLBACK_DURATION:
            return
        df = pd.DataFrame.from_dict(data)
        df['dt'] = pd.to_datetime(df['timestamp'], unit='ms')
        atr = df.ta.atr(9)
        c = data['close'][-1]
        if s.previous_peak and abs(c - s.previous_peak) > atr:
            logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}:回调盘整完成")
            s.pullback_detected = True
            self.status[symbol] = s

    def _detect_resistance_break(self, symbol, data):
        """检测突破上压力线"""
        if data['high'][-1] > self.status[symbol].resistance_level:
            logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}:突破压力线: {self.status[symbol].resistance_level}")
            s = self.status[symbol]
            s.crossover = True
            self.status[symbol] = s

    def _check_trend_reversal(self, symbol, data):
        """确认反转趋势"""
        df = pd.DataFrame.from_dict(data)
        df['dt'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['sma_f'] = df.ta.ema(20)
        df['sma_s'] = df.ta.ema(30)

        if df['sma_f'].iloc[-1] > df['sma_s'].iloc[-1] and df['sma_f'].iloc[-2] <= df['sma_s'].iloc[-2]:
            logging.info(f"{datetime.fromtimestamp(data['timestamp'][-1]/1000)} {symbol}:形成反转趋势，多头信号")
            return Direct.LONG
        return Direct.NONE
    
class ShortTermStrategy(Strategy):
    Params = namedtuple('Params', ['rsi_period', 'rsi_oversold', 'rsi_overbought', 'bb_period', 'bb_dev'])
    params = Params(rsi_period=14, rsi_oversold=20, rsi_overbought=80, bb_period=20, bb_dev=2.0)

    def __init__(self):
        super().__init__(self.__class__.__name__.replace("Strategy", ""), ['1m', '15m'], ['BTCUSDT', 'BNBUSDT', 'ETHUSDT', '1000PEPEUSDT', 'CAKEUSDT'])
    
    def analyse_data(self, symbol, data, interval):
        if len(data['timestamp']) < self.params.rsi_period or len(data['timestamp']) < self.params.bb_period:
            return StrategySignal(Direct.NONE)
        
        df = pd.DataFrame.from_dict(data)
        df['dt'] = pd.to_datetime(df['timestamp'], unit='ms')
        rsi = df.ta.rsi(self.params.rsi_period)
        bb = df.ta.bbands(self.params.bb_period, std=self.params.bb_dev)
        if not bb.empty:
            bbup = bb[f'BBU_{self.params.bb_period}_{self.params.bb_dev}_{self.params.bb_dev}']
            bblow = bb[f'BBL_{self.params.bb_period}_{self.params.bb_dev}_{self.params.bb_dev}']
        
            # Entry condition
            if symbol in self.symbols and rsi.iloc[-1] < self.params.rsi_oversold and data['close'][-1] < bblow.iloc[-1]:
                return StrategySignal(Direct.LONG)
            elif symbol in self.symbols  and rsi.iloc[-1] > self.params.rsi_overbought and data['close'][-1] > bbup.iloc[-1]:
                return StrategySignal(Direct.SHORT)
        
        return StrategySignal(Direct.NONE)
    
    def format_msg(self, signal):
        msg = self.template.content.replace("{symbol}", signal.symbol)
        msg = msg.replace("{{direction}}", "超卖" if signal.direct == Direct.LONG else "超买" )
        return msg
    
class PreviousPeakTroughStrategy(Strategy):
    def __init__(self):
        super().__init__(self.__class__.__name__.replace("Strategy", ""), ['1m', '15m'], ['BTCUSDT', 'BNBUSDT', 'ETHUSDT', 'SOLUSDT'])
        self.period_between_peaks_or_troughs = 4 * 12
        self.atr_pct = 0.5
        self.data_15m ={}
        self.period = 4 * 4 # 15m*4*4
        self.finder = PreviousPeakTroughFinder(self.period_between_peaks_or_troughs, self.period)

    def analyse_data(self, symbol, data, interval):
        if interval == "15m":
            self.data_15m[symbol] = pd.DataFrame.from_dict(data)
            return StrategySignal(Direct.NONE)
        
        if symbol not in self.data_15m:
            return StrategySignal(Direct.NONE)
        
        if self.data_15m[symbol].size == 0:
            return StrategySignal(Direct.NONE)
        
        if len(self.data_15m[symbol]['timestamp']) < self.period_between_peaks_or_troughs * 2:
            return StrategySignal(Direct.NONE)
        
        df = self.data_15m[symbol]
        atr = df.ta.atr(self.period_between_peaks_or_troughs * 2)
        c = data['close'][-1]
        value = self.finder.find(df, c)
        if value:
            if value.is_peak:
                if abs(value.previous_value - c) < self.atr_pct * atr.iloc[-1]*0.5:
                    return StrategySignal(Direct.SHORT, {'price': value.previous_value})
            else:    
                if abs(value.previous_value - c) < self.atr_pct * atr.iloc[-1]*0.5:
                    return StrategySignal(Direct.LONG, {'price': value.previous_value})
                
        return StrategySignal(Direct.NONE)
    
    def format_msg(self, signal):
        msg = self.template.content.replace("{symbol}", signal.symbol)
        msg = msg.replace("{{up/down}}", f"下方支撑({signal.params['price']})" if signal.direct == Direct.LONG else f"上方压力({signal.params['price']})" )
        msg = msg.replace("{{price}}", str(signal.data['close'][-1]) )
        return msg

if __name__ == "__name__":
    import glob
    symbol = 'xx'
    all_files = sorted(glob.glob(f"/Users/gongzuoyonghu/Documents/code/blockchain/bot/quantitative_trading/daily/{symbol}-1m-2024-1*.csv"))
    df = (pd.read_csv(file,
                            names=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number', 'taker', 'quo', 'i'],
                            header=None,
                            parse_dates=True,
                            index_col=None) for file in all_files)
    print(df)
    strategy = ShortTermStrategy()
    for d in df:
        strategy.analyse_data(symbol, df, "15m")

