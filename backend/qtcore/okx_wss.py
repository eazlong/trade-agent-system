"""
@author:    tz_zs
"""
import math
from websocket import WebSocketApp
import json
import copy
import time

import logging
from .okx_helper import CommonOkxHelper

class OkxWSSHelper:
    @staticmethod
    def load_data(symbol, interval, max_duration):
        try:
            bars = CommonOkxHelper().fetch_data(symbol, interval, limit=max_duration)
            return {'timestamp':[b[0] for b in bars], 'open': [b[1] for b in bars], 'high':[b[2] for b in bars], 'low': [b[3] for b in bars], 'close':[b[4] for b in bars], 'volume':[b[5] for b in bars] }
        except Exception as e:
            logging.error(f'OkxWSSHelper load_data error: {e}')
            return {}

class OkxWSS(object):
    def __init__(self, interval_config):
        # OKX WebSocket URL for public channels
        self.url = "wss://ws.okx.com:8443/ws/v5/public"
        self.ws = None
        self.interval_config = interval_config  
        self.tickers = {}
        for i in interval_config:
            self.tickers[i] = {}
    
    def on_message(self, wsapp, message):
        try:
            data = json.loads(message)
            
            # Check if it's a ticker message
            if 'data' in data and data.get('arg', {}).get('channel') == 'tickers':
                tickers_data = data['data']
                
                # Process each ticker
                for ticker in tickers_data:
                    # OKX uses instId for symbol
                    symbol = ticker['instId']
                    
                    # Skip non-USDT pairs if needed
                    if not symbol.endswith('USDT-SWAP'):
                        continue
                    
                    # Process for each interval
                    for interval, config in self.interval_config.items():
                        time_unit = config['time']
                        tickers = self.tickers[interval]
                        queue = config['queue']
                        md = config['max_duration']
                        
                        # Convert timestamp to milliseconds and round to interval
                        timestamp = int(ticker['ts'])
                        t = math.floor(timestamp / 1000 / 60 / time_unit) * 60 * 1000 * time_unit
                        
                        symbol_data = tickers.get(symbol)
                        
                        # Initialize data for new symbol
                        if symbol_data is None:
                            symbol_data = OkxWSSHelper.load_data(symbol, interval, md)
                            if not symbol_data:
                                symbol_data = {
                                    'timestamp': [t], 
                                    'open': [float(ticker['last'])], 
                                    'high': [float(ticker['last'])], 
                                    'low': [float(ticker['last'])], 
                                    'close': [float(ticker['last'])], 
                                    'volume': [float(ticker['vol24h'])]
                                }
                            tickers[symbol] = symbol_data
                        else:
                            # Manage data size
                            if len(symbol_data['timestamp']) > md:
                                for key in ['timestamp', 'open', 'high', 'low', 'close', 'volume']:
                                    symbol_data[key].pop(0)
                            
                            # Update existing candle or create new one
                            if symbol_data['timestamp'][-1] == t:
                                symbol_data['close'][-1] = float(ticker['last'])
                                symbol_data['low'][-1] = min(symbol_data['low'][-1], float(ticker['last']))
                                symbol_data['high'][-1] = max(symbol_data['high'][-1], float(ticker['last']))
                            else:
                                if len(symbol_data) > 1:
                                    symbol_data['close'][-1] = float(ticker['last'])
                                    # OKX provides 24h volume, so we need to approximate
                                    symbol_data['volume'][-1] = float(ticker['vol24h']) / 24 / 60 * time_unit
                                    symbol_data['low'][-1] = min(symbol_data['low'][-1], float(ticker['last']))
                                    symbol_data['high'][-1] = max(symbol_data['high'][-1], float(ticker['last']))
                                
                                # Put data in queue if we have enough history
                                if len(symbol_data['timestamp']) == md:
                                    queue.put_nowait({'symbol': symbol, 'data': copy.deepcopy(symbol_data)})
                                
                                # Add new candle
                                for key in ['timestamp', 'open', 'high', 'low', 'close', 'volume']:
                                    if key == 'timestamp':
                                        symbol_data[key].append(t)
                                    elif key == 'volume':
                                        symbol_data[key].append(float(ticker['vol24h']) / 24 / 60 * time_unit)
                                    else:
                                        symbol_data[key].append(float(ticker['last']))
                
        except Exception as e:
            logging.error(f"OkxWSS on_message error: {e}")

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
        
        # Subscribe to tickers for all USDT-SWAP pairs
        # OKX requires explicit subscription after connection
        subscription_message = {
            "op": "subscribe",
            "args": [
                {
                    "channel": "tickers",
                    "instType": "SWAP",
                    "instFamily": "USDT"
                }
            ]
        }
        
        wsapp.send(json.dumps(subscription_message))

    def start(self):
        logging.info(f"Starting OKX WebSocket connection...")
        try:
            # websocket.enableTrace(True)  # Enable for debugging
            self.ws = WebSocketApp(self.url,
                               on_open=self.on_open,
                               on_message=self.on_message,
                               on_error=self.on_error,
                               on_close=self.on_close)
            self.ws.run_forever()
        except Exception as e:
            logging.exception(e) 