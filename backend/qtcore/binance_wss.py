"""
@author:    tz_zs
"""
import math
import sqlite3
from websocket import WebSocketApp
import json
import copy
import time

import logging
# from enum import Enum
from .binance_helper import CommonBinanceHelper
# from .influxdb_helper import InfluxDBHelper
from .sqlite_helper import SQLiteHelper
import time
import datetime

class BinanceWSSHelper:
    @staticmethod
    def load_data(symbol, interval, max_duration, start_time):
        try:
            # First, try to fetch data from InfluxDB
            # influx_helper = InfluxDBHelper()
            # influx_data = influx_helper.query_klines(symbol, interval, limit=max_duration)
            # if influx_data and influx_data['timestamp']:
            #     logging.debug(f"Loaded {len(influx_data['timestamp'])} klines for {symbol} {interval} from InfluxDB.")
            #     return influx_data
            #calc start time

            sqlite_helper = SQLiteHelper()
            data = sqlite_helper.query_klines(symbol, interval, limit=max_duration*2, start_time=start_time)
            if data and data['timestamp'] and len(data['timestamp']) > 0:
                logging.debug(f"Loaded {len(data['timestamp'])} klines for {symbol} {interval} from SQLite. {datetime.datetime.fromtimestamp(data['timestamp'][-1]/1000)}")
                if len(data['timestamp']) < max_duration:
                    start_time = data['timestamp'][-1]
                else:
                    return data

            # If not in InfluxDB, fetch from Binance API
            logging.debug(f"No data in SQLite for {symbol} {interval} from {start_time}. Fetching from Binance API.")
            bars = CommonBinanceHelper().fetch_data(symbol, interval, limit=max_duration, since=start_time)
            if not bars:
                return {}
            
            if data and data['timestamp'] and len(data['timestamp']) > 0:
                for i, b in enumerate(bars):
                    if b[0] > data['timestamp'][-1]:
                        bars = bars[i:]
                        break

            # logging.info(f"Loaded {len(bars)} klines for {symbol} {interval} from Binance API.")
            # sqlite_helper.save_kline(symbol, interval, bars)
            
            if data:
                return {
                'timestamp': data['timestamp'] + [b[0] for b in bars],
                'open': data['open'] + [b[1] for b in bars],
                'high': data['high'] + [b[2] for b in bars],
                'low': data['low'] + [b[3] for b in bars],
                'close': data['close'] + [b[4] for b in bars],
                'volume': data['volume'] + [b[5] for b in bars]
            }
            # Format data to be consistent
            return {
                'timestamp': [b[0] for b in bars],
                'open': [b[1] for b in bars],
                'high': [b[2] for b in bars],
                'low': [b[3] for b in bars],
                'close': [b[4] for b in bars],
                'volume': [b[5] for b in bars]
            }
        except Exception as e:
            logging.error(f'BinanceWSSHelper load_data error: {e}')
            return {}

class BinanceWSS(object):
    def __init__(self, interval_config):
        self.url = "wss://fstream.binance.com/ws/!miniTicker@arr"
        self.ws = None
        self.interval_config = interval_config  
        self.tickers = {}
        for i in interval_config:
            self.tickers[i] = {}
    
    def on_message(self, wsapp, message):
        try:
            data = json.loads(message)
            usdt_symbols = [m for m in data if m['s'].endswith('USDT') and not m['s'].startswith('USDC')]
            for interval, config in self.interval_config.items():
                time_interval = config['time']
                tickers = self.tickers[interval]
                queue = config['queue']
                md = config['max_duration']

                for m in usdt_symbols:
                    t = math.floor(m['E'] / 1000 / 60 / time_interval) * 60 * 1000 * time_interval
                    symbol_data = tickers.get(m['s'])

                    if symbol_data is None:
                        start_time = int(time.time() - time_interval * md * 60) * 1000
                        symbol_data = BinanceWSSHelper.load_data(m['s'], interval, md, start_time)
                        if not symbol_data:
                            symbol_data = {'timestamp': [t], 'open': [float(m['c'])], 'high': [float(m['c'])], 'low': [float(m['c'])], 'close': [float(m['c'])], 'volume': [0]}
                        tickers[m['s']] = symbol_data
                    else:
                        if len(symbol_data['timestamp']) > md:
                            for key in ['timestamp', 'open', 'high', 'low', 'close', 'volume']:
                                symbol_data[key].pop(0)

                        if symbol_data['timestamp'][-1] == t:
                            symbol_data['close'][-1] = float(m['c'])
                            symbol_data['low'][-1] = min(symbol_data['low'][-1], float(m['c']))
                            symbol_data['high'][-1] = max(symbol_data['high'][-1], float(m['c']))
                        else:
                            if len(symbol_data) > 1:
                                symbol_data['close'][-1] = float(m['c'])
                                symbol_data['volume'][-1] = float(m['v']) - sum(symbol_data['volume'][:-1])
                                symbol_data['low'][-1] = min(symbol_data['low'][-1], float(m['c']))
                                symbol_data['high'][-1] = max(symbol_data['high'][-1], float(m['c']))

                            if len(symbol_data['timestamp']) == md:
                                queue.put_nowait({'symbol': m['s'], 'data': copy.deepcopy(symbol_data)})

                            for key in ['timestamp', 'open', 'high', 'low', 'close', 'volume']:
                                symbol_data[key].append(float(m['c']) if key != 'timestamp' else t)
                            
        except Exception as e:
            logging.error(f"BinanceWSS on_message error: {e}")

    def on_error(self, wsapp, error):
        logging.info("####### on_error #######")
        logging.info("error：%s" % error)

    def on_close(self, wsapp, close_status_code, close_msg):
        logging.info(f"####### on_close ####### {close_status_code} {close_msg}")
        time.sleep(1)
        self.start()

    def on_ping(self, wsapp, message):
        logging.info("####### on_ping #######")
        logging.info("ping message：%s" % message)

    def on_pong(self, wsapp, message):
        logging.info("####### on_pong #######")
        logging.info("pong message：%s" % message)

    def on_open(self, wsapp):
        logging.info("####### on_open #######")
        #message = {
        #    "id": "51e2affb-0aba-4821-ba75-f2625006eb43",
        #    "method": "depth",
        #    "params": {
        #        "symbol": "BNBBTC",
        #        "limit": 5
        #    }
        #}

        #self.ws.send(json.dumps(message))


    # def run(self, *args):
    #     while True:
    #         time.sleep(1)
    #         input_msg = input("输入要发送的消息（ps：输入关键词 close 结束程序）:\n")
    #         if input_msg == "close":
    #             self.ws.close()  # 关闭
    #             logging.info("thread terminating...")
    #             break
    #         else:
    #             self.ws.send(input_msg)

    def start(self):
        logging.info(f"start wss.............")
        try:
            url = "wss://fstream.binance.com/ws/!miniTicker@arr"
            # websocket.enableTrace(True)  # 开启运行状态追踪。debug 的时候最好打开他，便于追踪定位问题。
            self.ws = WebSocketApp(url,
                               on_open=self.on_open,
                               on_message=self.on_message,
                               on_error=self.on_error,
                               on_close=self.on_close)
        # self.ws.on_open = self.on_open  # 也可以先创建对象再这样指定回调函数。run_forever 之前指定回调函数即可。
            self.ws.run_forever()
        except Exception as e:
            logging.exception(e)


