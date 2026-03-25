import json
import logging
import time
import websocket
from qtcore.trade_helper_manager import TradeHelperManager

class BinancePrivateWebsocketClient:
    def __init__(self, user_id, queue):
        self.ws = None
        self.user_id = user_id
        self.helper = TradeHelperManager().helper(user_id)
        self.listen_key = self.helper.listen_key()
        self.url = f"wss://fstream.binance.com/ws/{self.listen_key}" if not self.helper.is_sandbox else f"wss://testnet.binance.vision/ws/{self.listen_key}"
        self.queue = queue
        self.last_listen_key_update = time.time()
        
    def connect(self):
        logging.info(f"connect to {self.url}")
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_ping=self.on_ping
        )
        # 获取listenKey
        self.ws.run_forever()
        
    def on_message(self, ws, message):
        data = json.loads(message)
        # logging.info(f"BinancePrivateWebsocketClient on_message: {data}")
        if data['e'] == 'executionReport':
            logging.info(f"BinancePrivateWebsocketClient on_message executionReport: {data}")
            self._handle_order(data)
        elif data['e'] == 'ORDER_TRADE_UPDATE':
            logging.debug(f"BinancePrivateWebsocketClient on_message ORDER_TRADE_UPDATE: {data}")
            self._handle_order_trade_update(data)
    
    def on_ping(self, ws, message):
        # 检查是否需要更新listen_key (每55分钟更新一次)
        # logging.info(f"on_ping: {message}")
        current_time = time.time()
        try:
            # 使用update_listen_key()方法延长listenKey的有效期
            self.helper.update_listen_key()
            self.last_listen_key_update = current_time
        except Exception as e:
            logging.error(f"Failed to update listen key: {e}")
            # 如果更新失败，尝试获取新的listenKey
            self.listen_key = self.helper.listen_key()
            self.last_listen_key_update = current_time
            self.url = f"wss://fstream.binance.com/ws/{self.listen_key}" if not self.helper.is_sandbox else f"wss://testnet.binance.vision/ws/{self.listen_key}"
            # 重新连接WebSocket
            self.ws.close()
            self.start()
    
    def _handle_order_trade_update(self, data):
        # Extract order data from the message
        o = data['o']
        self.queue.put(o)
                
    def _handle_order(self, data):
        pass
        # order = TbUserTradeRecord(
        #     symbol=data['s'],
        #     order_id=data['i'],
        #     client_order_id=data['c'],
        #     price=data['p'],
        #     quantity=data['q'],
        #     side=data['S'],
        #     type=data['o'],
        #     status=data['X'],
        #     timestamp=data['T']
        # )
        # order.save()
        
    def on_error(self, ws, error):
        print(f"Error: {error}")
        time.sleep(1)
        self.start()
        
    def on_close(self, wsapp, close_status_code, close_msg):
        print("WebSocket Connection Closed")
        
    def start(self):
        try:
            self.connect()
        except Exception as e:
            logging.exception(e)
            time.sleep(1)
            self.start()
