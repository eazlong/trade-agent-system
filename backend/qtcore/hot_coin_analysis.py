from .strategy import StrategySignalProcessor, Direct
from .wss_data_consumer_manager import WSSDataProcessorManager
from assistant.models.hotcoin import TbHotCoin
import time
import datetime
from utils.singleton import singleton
import logging
from qtcore.symbol_helper import SymbolHelper

@singleton
class HotCoinAnalysis(StrategySignalProcessor):
    VALID_PERIOD = 14 * 24 * 60 * 60
    class HotCoinSignals:
        def __init__(self, symbol, big_wave=[], first_wave={}, trend = [0, 0, 0, 0], supply = [0, 0], id=0):
            self.id = id
            self.symbol = symbol
            self.big_wave_signal = big_wave
            self.first_wave_signal = first_wave
            self.trend = trend # 0: 无趋势, 1: 上涨趋势, -1: 下跌趋势 [15m, 1h, 4h, 1d]
            self.supply = supply # [total_supply, circulating_supply]
        
        def add_big_wave_signal(self, signal):
            self.big_wave_signal.append({
                "signal": signal,
                "timestamp": time.time()
            })
            self.clean_old_signals()

        def clean_old_signals(self):
            """删除以前的记录"""
            current_time = time.time()
            three_hours_in_seconds = HotCoinAnalysis.VALID_PERIOD
            # 过滤掉HotCoinAnalysis.VALID_PERIOD前的big_wave_signal记录
            self.big_wave_signal = [
                signal for signal in self.big_wave_signal 
                if current_time - signal["timestamp"] < three_hours_in_seconds
            ]

        def set_first_wave_signal(self, signal):
            self.first_wave_signal = {
                "signal": signal,
                "price": signal.params['peak'],
                "timestamp": time.time()
            }

        def set_trend(self, trend, period):
            if period == "15m":
                self.trend[0] = trend
            elif period == "1h":
                self.trend[1] = trend
            elif period == "4h":
                self.trend[2] = trend
            elif period == "1d":    
                self.trend[3] = trend
                
    def __init__(self):
        super().__init__("HotCoinAnalysis")
        self.hot_coin_list = {}
    
    def start(self):
        self.first_wave_strategy = WSSDataProcessorManager().get_consumers("FirstWave")
        self.big_wave_strategy = WSSDataProcessorManager().get_consumers("BigWave")
        self.trend_analysis_strategy = WSSDataProcessorManager().get_consumers("TrendAnalysis")
        self.first_wave_strategy.register(-1, self)
        self.big_wave_strategy.register(-1, self)
        self.trend_analysis_strategy.register(-1, self)

    def do(self, signal):
        try:
            if signal.symbol not in self.hot_coin_list:
                try:
                    hc = TbHotCoin.objects.get(symbol=signal.symbol)
                except TbHotCoin.DoesNotExist:
                    hc = None

                if hc:
                    self.hot_coin_list[signal.symbol] = self.HotCoinSignals(signal.symbol, [], 
                                                                        {"price": hc.break_price, "timestamp": hc.break_time}, 
                                                                        [hc.trend_15m, hc.trend_1h, hc.trend_4h, hc.trend_1d],
                                                                        [hc.total_supply, hc.circulating_supply], hc.id)
                else:
                    self.hot_coin_list[signal.symbol] = self.HotCoinSignals(signal.symbol)

            # Convenience reference
            coin_obj = self.hot_coin_list[signal.symbol]

            if signal.strategy == "FirstWave":
                if signal.direct == Direct.LONG:
                    coin_obj.set_first_wave_signal(signal)

            if signal.strategy == "BigWave":
                if signal.direct == Direct.LONG:
                    coin_obj.add_big_wave_signal(signal)
            
            if coin_obj.supply[0] == 0 :
                symbol_info = SymbolHelper().get_symbol_info(signal.symbol)
                coin_obj.supply = [symbol_info['total_supply'], symbol_info['circulating_supply']]

            logging.info(f"HotCoinAnalysis: {signal.symbol} {signal.interval} {signal.strategy} {signal.direct} {signal.params}")
            
            # Persist the updated coin_obj to the database
        
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            TbHotCoin.objects.update_or_create(
                symbol=coin_obj.symbol,
                defaults={
                    "trend_15m": coin_obj.trend[0],
                    "trend_1h": coin_obj.trend[1],
                    "trend_4h": coin_obj.trend[2],
                    "trend_1d": coin_obj.trend[3],
                    "break_time": coin_obj.first_wave_signal.get("timestamp", 0) if coin_obj.first_wave_signal else 0,
                    "break_price": coin_obj.first_wave_signal.get("price", 0) if coin_obj.first_wave_signal else 0,
                    "big_wave_count": len(coin_obj.big_wave_signal),
                    "total_supply": coin_obj.supply[0],
                    "circulating_supply": coin_obj.supply[1],
                    "updated_at": now if coin_obj.id > 0 else now,
                },
            )
        except Exception as e:
            # Silently ignore DB errors to avoid breaking real-time processing
            logging.error(f"Error persisting hot coin {coin_obj.symbol}: {e}")
            pass

    # def get_hot_coin_list(self):
    #     sorted_hot_coin_list = sorted(self.hot_coin_list.values(), key=lambda x: len(x.big_wave_signal), reverse=True)
    #     sorted_hot_coin_list = [coin for coin in sorted_hot_coin_list if len(coin.first_wave_signal) > 0 and coin.first_wave_signal["timestamp"] > time.time() - self.VALID_PERIOD]
    #     if "ETHUSDT" in self.hot_coin_list:
    #         sorted_hot_coin_list.insert(0, self.hot_coin_list["ETHUSDT"])
    #     if "BTCUSDT" in self.hot_coin_list:
    #         sorted_hot_coin_list.insert(0, self.hot_coin_list["BTCUSDT"])
    #     return sorted_hot_coin_list

    def destroy(self):
        pass