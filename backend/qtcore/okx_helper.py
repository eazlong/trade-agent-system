import ccxt
import time
import logging
from utils.singleton import singleton
from ccxt.base.errors import OrderNotFound

from enum import Enum
class StatusCode(Enum):
    FAIL = 0
    SUCCESS = 1
    PROCESSING = 2


class SingleOkxHelper:
    def __init__(self):
        self._exchange = ccxt.okx({
            "enableRatelimit": True,
            'options': {
                'defaultType': 'swap',  # 指定交易类型为合约
            }
        })
        self._exchange.load_markets()

    @property
    def exchange(self):
        return self._exchange
    
    def fetch_data(self, symbol, timeframe, limit=0, since=None, end_time=None):
        if end_time and since:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since, params={"before": end_time})
        elif since:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since)
        else:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_price(self, symbol):
        ticker = self.exchange.fetch_ticker(symbol)
        return ticker['last']
    
    def all_symbol(self):
        return self.exchange.symbols


@singleton
class CommonOkxHelper(SingleOkxHelper):
    def __init__(self):
        super().__init__()


class OkxHelper(SingleOkxHelper):
    def __init__(self, apikey, secret, password, options='swap', sandbox=False):
        self._exchange = ccxt.okx({
            "apiKey": apikey,
            "secret": secret,
            "password": password,  # OKX requires a passphrase/password
            "enableRatelimit": True,
            'options': {
                'defaultType': options,  # 指定交易类型为合约
            }
        })
        if sandbox:
            self._exchange.set_sandbox_mode(True)
        self._is_sandbox = sandbox
        self._exchange.load_markets()

    @property
    def is_sandbox(self):
        return self._is_sandbox

    def listen_key(self):
        # OKX uses a different approach for WebSocket authentication
        # This is a placeholder - actual implementation depends on OKX API specifics
        return None

    def update_listen_key(self):
        # OKX doesn't require listen key updates like Binance
        pass


class OkxAccountHelper():
    def __init__(self, helper): 
        self.helper = helper
        self._account = self._get_account()

    def _get_account(self):
        return self.helper.exchange.fetch_balance()
    
    @property
    def account(self):
        return self._account

    def position_mode(self, symbol):
        # OKX equivalent for position mode check
        resp = self.helper.exchange.privateGetAccountPositionMode()
        logging.info(f"resp {resp}")
        return resp.get("posMode", "one-way") == "hedge"
        
    def isolated(self, symbol):
        positions = self.helper.exchange.fetch_positions([symbol])
        for p in positions:
            if p['symbol'] == symbol:
                return p['marginMode'] == 'isolated'
        return False

    def all_orders(self, symbol, since=None):
        symbol = symbol.replace("USDT", "-USDT-SWAP")
        params = {'instType': 'SWAP'}
        if since:
            params['start'] = since
        order_book = self.helper.exchange.fetchClosedOrders(symbol=symbol, params=params)
        return order_book

    def all_symbols(self):
        return self.helper.all_symbol()

    def fetch_user_trades(self, symbol=None, since=None, limit=None, from_id=None):
        """
        获取用户历史成交记录
        :param symbol: 交易对 (optional)
        :param since: 起始时间戳
        :param limit: 数量限制
        :param from_id: 从哪个id开始获取
        :return:
        """
        params = {'instType': 'SWAP'}
        if from_id:
            params['fromId'] = from_id
        return self.helper.exchange.fetch_my_trades(symbol=symbol, since=since, limit=limit, params=params)

    def change_margin_type(self, symbol, type="ISOLATED"):
        current_isolated = self.isolated(symbol)
        if (not current_isolated and type == "ISOLATED") or (current_isolated and type != "ISOLATED"):
            margin_mode = "isolated" if type == "ISOLATED" else "cross"
            return self.helper.exchange.privatePostAccountSetLeverage({
                "instId": symbol,
                "mgnMode": margin_mode
            })
        return True
    
    def _get_positions(self, symbol, side='long'):
        positions = self.helper.exchange.fetch_positions([symbol])

        for p in positions:
            if p['symbol'] == symbol and p['side'].lower() == side.lower():
                logging.info(f"{symbol} position : {p['contracts']}")
                return float(p['contracts'] or 0)
        return 0.0

    def position(self, symbol, side='long'):
        return self._get_positions(symbol, side)

    def position_risk(self, symbol=None):
        """查询指定交易对的持仓风险信息
        
        Args:
            symbol: 交易对符号，如'BTC-USDT-SWAP'
            
        Returns:
            dict: 包含持仓风险信息的字典
        """
        if symbol:
            positions = self.helper.exchange.fetch_positions([symbol])
            for position in positions:
                if position['symbol'] == symbol:
                    return {
                        'positionAmt': float(position['contracts'] or 0),
                        'positionSide': position['side'],
                        'leverage': int(position['leverage']),
                        'unRealizedProfit': float(position['unrealizedPnl']),
                        'maintenanceMarginRate': float(position['maintenanceMargin']),
                        'liquidationPrice': float(position['liquidationPrice'] or 0)
                    }
            return None
        return self.helper.exchange.fetch_positions()

    def balance(self):
        balance = self.helper.exchange.fetch_balance()
        logging.debug(f'balance info: {balance["USDT"]}')
        return balance['USDT']['free'], balance['USDT']['total']


class OkxTradeHelper:
    def __init__(self, symbol, helper, margin_type="ISOLATED"):
        self.helper = helper
        self.exchange = self.helper.exchange
        self.account_helper = OkxAccountHelper(helper)
        self.account_helper.change_margin_type(symbol, type=margin_type)
        self.symbol = symbol
        
        # Get symbol info for precision
        symbol_info = self._get_symbol_info()
        self._price_precision = int(symbol_info['precision']['price'])
        self._quantity_precision = int(symbol_info['precision']['amount'])

        # 设置为双向持仓模式
        try:
            # 检查当前持仓模式
            current_position_mode = self.account_helper.position_mode(self.symbol)
            # 如果不是双向持仓模式，则切换为双向持仓模式
            if not current_position_mode:
                logging.info(f"Switching to dual position mode for {self.symbol}")
                self.exchange.privatePostAccountSetPositionMode({
                    "posMode": "hedge"  # OKX uses 'hedge' for dual position mode
                })
                logging.info(f"Successfully switched to dual position mode for {self.symbol}")
            else:
                logging.debug(f"Account already in dual position mode for {self.symbol}")
        except Exception as e:
            logging.exception(e)
            logging.error(f"Failed to set dual position mode for {self.symbol}: {str(e)}")
        
    def _get_symbol_info(self):
        markets = self.exchange.markets
        if self.symbol in markets:
            return markets[self.symbol]
        raise Exception(f"{self.symbol} not supported")
    
    def close_all_positions(self, symbol):
        long_position = self.account_helper.position(symbol, side='long')
        short_position = self.account_helper.position(symbol, side='short')

        if long_position > 0:
            self.okx_market_sell(long_position, positionSide='long')
        
        if short_position > 0:
            self.okx_market_buy(short_position, positionSide='short')
    
    def usdt2amount(self, usdt):
        price = self.helper.fetch_price(self.symbol)
        return round(usdt/price, self._quantity_precision)

    def okx_limit_buy(self, amount, price):
        amount = round(float(amount), self._quantity_precision)
        price = round(float(price), self._price_precision)
        return self.exchange.create_limit_buy_order(self.symbol, amount, price)

    def okx_limit_sell(self, amount, price):
        amount = round(float(amount), self._quantity_precision)
        price = round(float(price), self._price_precision)
        return self.exchange.create_limit_sell_order(self.symbol, amount, price)

    def okx_stop_market(self, amount, stopPrice, side='sell', posSide='long'):
        amount = round(float(amount), self._quantity_precision)
        stopPrice = round(float(stopPrice), self._price_precision)
        
        params = {
            'stopPrice': stopPrice,
            'posSide': posSide.upper(),  # OKX requires position side
            'reduceOnly': True  # To ensure it's a closing order
        }
        
        return self.exchange.create_order(
            symbol=self.symbol,
            type='stop_market',
            side=side,
            amount=amount,
            price=None,
            params=params
        )

    def okx_take_profit_market(self, amount, stopPrice, side='sell', posSide='long'):
        amount = round(float(amount), self._quantity_precision)
        stopPrice = round(float(stopPrice), self._price_precision)
        
        params = {
            'stopPrice': stopPrice,
            'posSide': posSide.upper(),
            'reduceOnly': True,
            'ordType': 'take_profit_market'
        }
        
        return self.exchange.create_order(
            symbol=self.symbol,
            type='take_profit_market',
            side=side,
            amount=amount,
            price=None,
            params=params
        )

    def okx_stop_limit(self, amount, stopPrice, price, side='sell', posSide='long'):
        amount = round(float(amount), self._quantity_precision)
        stopPrice = round(float(stopPrice), self._price_precision)
        price = round(float(price), self._price_precision)
        
        params = {
            'stopPrice': stopPrice,
            'posSide': posSide.upper(),
            'reduceOnly': True
        }
        
        return self.exchange.create_order(
            symbol=self.symbol,
            type='stop_limit',
            side=side,
            amount=amount,
            price=price,
            params=params
        )

    def okx_take_profit_limit(self, amount, stopPrice, price, side='sell', posSide='long'):
        amount = round(float(amount), self._quantity_precision)
        stopPrice = round(float(stopPrice), self._price_precision)
        price = round(float(price), self._price_precision)
        
        params = {
            'stopPrice': stopPrice,
            'posSide': posSide.upper(),
            'reduceOnly': True,
            'ordType': 'take_profit'
        }
        
        return self.exchange.create_order(
            symbol=self.symbol,
            type='take_profit',
            side=side,
            amount=amount,
            price=price,
            params=params
        )

    def okx_market_sell(self, amount, positionSide):
        amount = round(float(amount), self._quantity_precision)
        return self.exchange.create_order(
            symbol=self.symbol,
            type='market',
            side='sell',
            amount=amount,
            params={"posSide": positionSide.upper()}
        )

    def okx_market_buy(self, amount, positionSide='long', margin_type='isolated'):
        amount = round(float(amount), self._quantity_precision)
        return self.exchange.create_market_buy_order(
            self.symbol, 
            amount, 
            params={
                'posSide': positionSide.upper(),
                'tdMode': margin_type.lower()  # OKX uses tdMode for margin type
            }
        )
    
    def okx_create_order(self, quantity, price, stop_price, take_profit_price, side='buy', margin_type='isolated'):
        quantity = round(float(quantity), self._quantity_precision)
        price = round(float(price), self._price_precision)
        stop_price = round(float(stop_price), self._price_precision)
        take_profit_price = round(float(take_profit_price), self._price_precision)
        
        # OKX handles this differently - we need to place separate orders
        # First place the main order
        main_order = self.exchange.create_order(
            symbol=self.symbol,
            type='market',
            side=side,
            amount=quantity,
            params={
                'posSide': 'LONG' if side.lower() == 'buy' else 'SHORT',
                'tdMode': margin_type.lower()
            }
        )
        
        # Then place stop loss
        stop_params = {
            'stopPrice': stop_price,
            'posSide': 'LONG' if side.lower() == 'buy' else 'SHORT',
            'reduceOnly': True
        }
        
        opposite_side = 'sell' if side.lower() == 'buy' else 'buy'
        
        self.exchange.create_order(
            symbol=self.symbol,
            type='stop_market',
            side=opposite_side,
            amount=quantity,
            params=stop_params
        )
        
        # Then place take profit
        tp_params = {
            'stopPrice': take_profit_price,
            'posSide': 'LONG' if side.lower() == 'buy' else 'SHORT',
            'reduceOnly': True,
            'ordType': 'take_profit_market'
        }
        
        self.exchange.create_order(
            symbol=self.symbol,
            type='take_profit_market',
            side=opposite_side,
            amount=quantity,
            params=tp_params
        )
        
        return main_order

    def cancel_order(self, oid=None, client_order_id=None):
        params = {"instId": self.symbol}
        
        if oid:
            logging.info(f'cancel order {oid}')
            params["ordId"] = oid
            return self.exchange.cancel_order(id=oid, symbol=self.symbol, params=params)
        elif client_order_id:
            logging.info(f'cancel order {client_order_id}')
            params["clOrdId"] = client_order_id
            return self.exchange.cancel_order(id=None, symbol=self.symbol, params=params)

    def wait_result(self, oid, timeout=5, cancel=True):
        t = 0
        time.sleep(0.1)
        order_id = oid['id']
        
        while True:
            try:
                order = self.exchange.fetch_order(order_id, self.symbol)
                if order['status'] != "filled":
                    if t >= timeout:
                        if cancel:
                            try:
                                self.exchange.cancel_order(order_id, self.symbol)
                            except OrderNotFound as e:
                                continue
                        return StatusCode.FAIL
                    t += 1
                    time.sleep(0.1)
                    continue
                else:
                    logging.info(f'------okx {self.symbol}开单成功-----------')
                    return StatusCode.SUCCESS
            except Exception as e:
                logging.error(f"Error checking order status: {str(e)}")
                return StatusCode.FAIL

    def query_orders(self):
        order_book = self.exchange.fetch_open_orders(self.symbol)
        logging.debug(f'open orders: {order_book}')
        return order_book
            
    def place_trailing_stop_market_order(self, symbol, quantity, side='long'):
        try:
            # OKX uses a different parameter structure for trailing stops
            trailing_params = {
                'posSide': side.upper(),
                'reduceOnly': True,
                'callbackRatio': 0.5  # 0.5% callback rate
            }
            
            opposite_side = 'sell' if side.lower() == 'long' else 'buy'
            
            # 下单
            order = self.exchange.create_order(
                symbol,
                'trailing_stop',
                opposite_side,
                quantity,
                None,
                trailing_params
            )
            
            return True
        except Exception as e:
            logging.exception(e)
            logging.error(f'下单失败: {str(e)}')
            return False

    def create_o2o_market_order(self, side, quantity, price, stop_price, take_profit_price):
        quantity = round(float(quantity), self._quantity_precision)
        price = round(float(price), self._price_precision)
        stop_price = round(float(stop_price), self._price_precision)
        take_profit_price = round(float(take_profit_price), self._price_precision)
        
        # OKX doesn't support the exact same OCO functionality as Binance
        # We'll need to place separate orders
        
        # Place the main limit order
        main_order = self.exchange.create_order(
            symbol=self.symbol,
            type='limit',
            side=side,
            amount=quantity,
            price=price,
            params={
                'posSide': 'LONG',
                'tdMode': 'isolated'
            }
        )
        
        # Place the stop loss order
        opposite_side = 'sell' if side.lower() == 'buy' else 'buy'
        
        self.exchange.create_order(
            symbol=self.symbol,
            type='stop_market',
            side=opposite_side,
            amount=quantity,
            params={
                'stopPrice': stop_price,
                'posSide': 'LONG',
                'reduceOnly': True
            }
        )
        
        return main_order
