import json
import logging
import time
import websocket
import hmac
import base64
import datetime
from urllib.parse import urlencode
from trade.models import TbUserTradeRecord
from qtcore.trade_helper_manager import TradeHelperManager

class OkxPrivateWebsocketClient:
    def __init__(self, user_id, queue):
        self.ws = None
        self.user_id = user_id
        self.helper = TradeHelperManager().helper(user_id, exchange_type='okx')  # Assuming TradeHelperManger can handle OKX
        self.url = "wss://ws.okx.com:8443/ws/v5/private" if not self.helper.is_sandbox else "wss://wspap.okx.com:8443/ws/v5/private"
        self.queue = queue
        self.api_key = self.helper.exchange.apiKey
        self.api_secret = self.helper.exchange.secret
        self.passphrase = self.helper.exchange.password
        
    def _generate_signature(self):
        """Generate OKX API signature for WebSocket authentication"""
        timestamp = datetime.datetime.utcnow().isoformat()[:-3] + 'Z'
        message = timestamp + 'GET' + '/users/self/verify'
        
        mac = hmac.new(
            bytes(self.api_secret, encoding='utf8'),
            bytes(message, encoding='utf-8'),
            digestmod='sha256'
        )
        d = mac.digest()
        signature = base64.b64encode(d).decode()
        
        return timestamp, signature
    
    def connect(self):
        logging.info(f"Connecting to OKX private WebSocket: {self.url}")
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        self.ws.run_forever()
        
    def on_open(self, ws):
        """Handle WebSocket connection open and authenticate"""
        timestamp, signature = self._generate_signature()
        
        # Authenticate with OKX WebSocket API
        auth_message = {
            "op": "login",
            "args": [{
                "apiKey": self.api_key,
                "passphrase": self.passphrase,
                "timestamp": timestamp,
                "sign": signature
            }]
        }
        
        ws.send(json.dumps(auth_message))
        
        # Subscribe to order updates after authentication
        # Wait a short time to ensure authentication is processed
        time.sleep(1)
        
        # Subscribe to order updates
        subscription_message = {
            "op": "subscribe",
            "args": [
                {
                    "channel": "orders",
                    "instType": "SWAP"
                },
                {
                    "channel": "orders-algo",
                    "instType": "SWAP"
                }
            ]
        }
        
        ws.send(json.dumps(subscription_message))
        
    def on_message(self, ws, message):
        data = json.loads(message)
        logging.info(f"OkxPrivateWebsocketClient on_message: {data}")
        
        # Handle login response
        if data.get('event') == 'login':
            if data.get('code') == '0':
                logging.info("OKX WebSocket login successful")
            else:
                logging.error(f"OKX WebSocket login failed: {data}")
                
        # Handle subscription response
        elif data.get('event') == 'subscribe':
            logging.info(f"OKX WebSocket subscription response: {data}")
            
        # Handle order updates
        elif 'data' in data and data.get('arg', {}).get('channel') in ['orders', 'orders-algo']:
            for order_data in data['data']:
                self._handle_order_update(order_data)
    
    def _handle_order_update(self, order_data):
        """Process order update data"""
        # Put raw order data in queue for processing by consumers
        self.queue.put(order_data)
        
        # Also save to database if needed
        try:
            # Map OKX order status to a common format
            status_mapping = {
                'live': 'NEW',
                'partially_filled': 'PARTIALLY_FILLED',
                'filled': 'FILLED',
                'canceled': 'CANCELED',
                'mmp_canceled': 'CANCELED',
                'failed': 'REJECTED'
            }
            
            # Map OKX order side to a common format
            side_mapping = {
                'buy': 'BUY',
                'sell': 'SELL'
            }
            
            order = TbUserTradeRecord(
                symbol=order_data['instId'],
                order_id=order_data['ordId'],
                client_order_id=order_data.get('clOrdId', ''),
                price=float(order_data.get('px', 0)),
                quantity=float(order_data.get('sz', 0)),
                side=side_mapping.get(order_data.get('side', '').lower(), order_data.get('side', '')),
                type=order_data.get('ordType', ''),
                status=status_mapping.get(order_data.get('state', '').lower(), order_data.get('state', '')),
                timestamp=int(order_data.get('uTime', time.time() * 1000))
            )
            order.save()
        except Exception as e:
            logging.error(f"Error saving order to database: {e}")
        
    def on_error(self, ws, error):
        logging.error(f"OKX WebSocket error: {error}")
        time.sleep(1)
        self.start()
        
    def on_close(self, wsapp, close_status_code, close_msg):
        logging.info(f"OKX WebSocket connection closed: {close_status_code} - {close_msg}")
        time.sleep(1)
        self.start()
        
    def start(self):
        try:
            self.connect()
        except Exception as e:
            logging.exception(f"Error connecting to OKX WebSocket: {e}")
            time.sleep(1)
            self.start() 