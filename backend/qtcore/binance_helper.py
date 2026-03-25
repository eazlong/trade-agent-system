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


class SingleBinanceHelper:
    def __init__(self):
        self._exchange = ccxt.binance({
            "enableRatelimit": True,
            'options': {
                'defaultType': 'future',  # 指定交易类型为合约
            }
        })
        self._exchange.load_markets()

    @property
    def exchange(self):
        return self._exchange
    
    def fetch_data(self, symbol, timeframe, limit=0, since=None, end_time=None):
        if end_time and since:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since, params={"until": end_time})
        elif since:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since)
        else:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_price(self, symbol):
        ticker = self.exchange.fetch_ticker(symbol)
        return ticker['last']
    
    def all_symbol(self):
        return self.exchange.symbols;


@singleton
class CommonBinanceHelper(SingleBinanceHelper):
    def __init__(self):
        super().__init__()

class BinanceHelper(SingleBinanceHelper):
    def __init__(self, apikey, secret, options='future', sandbox=False):
        self._exchange = ccxt.binance({
            "apiKey": apikey,
            "secret": secret,
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
        key = self._exchange.fapiPrivatePostListenKey()
        return key['listenKey']

    def update_listen_key(self):
        self._exchange.fapiPrivatePutListenKey()

class BinanceAccountHelper():
    def __init__(self, helper): 
        self.helper = helper
        self._account = self._get_account()

    def _get_account(self):
        return self.helper.exchange.fapiPrivateV2GetAccount({'timestamp': int(time.time())*1000})
    
    @property
    def account(self):
        return self._account

    def position_mode(self, symbol):
        resp = self.helper.exchange.fetchPositionMode(symbol)
        logging.debug(f"resp {resp}" )
        return resp.get("hedged", False)
        
    def isolated(self, symbol):
        for p in self._account['positions']:
            if p['symbol'] == symbol:
                return p['isolated']

    def all_orders(self, symbol, since=None):
        params = {'type': 'future'}
        if since:
            params['since'] = since
        order_book = self.helper.exchange.fetch_orders(symbol=symbol, params=params)
        return order_book

    def all_symbols(self):
        return self.helper.all_symbol()
    
    def get_income_history(self):
        return self.helper.exchange.fapiPrivateGetIncome({'incomeType': 'REALIZED_PNL', 'timestamp': int(time.time())*1000, 'limit': 1000, 'startTime': int(time.time())*1000 - 100*24*60*60*1000})

    def fetch_user_trades(self, symbol=None, since=None, limit=None, from_id=None):
        """
        获取用户历史成交记录
        :param symbol: 交易对 (optional)
        :param since: 起始时间戳
        :param limit: 数量限制
        :param from_id: 从哪个id开始获取
        :return:
        """
        params = {}
        if from_id:
            params['fromId'] = from_id
        return self.helper.exchange.fetch_my_trades(symbol=symbol, since=since, limit=limit, params=params)

    def change_margin_type(self, symbol, type="ISOLATED"):
        try:
            # m = self.helper.exchange.fapiPrivateGetMarginType()
            # if m['marginType'] == type:
            #     return True
                
            return self.helper.exchange.fapiPrivatePostMarginType({"symbol": symbol, "marginType": type})
        except Exception as e:
            # logging.exception(e)
            # logging.error(f"Failed to change margin type for {symbol}: {str(e)}")
            return False
    
    def _get_positions(self, symbol, side='LONG'):
        positions = self.helper.exchange.fetch_positions(params={'type': 'future'})

        for p in positions:
            i = p['info']
            if i['symbol'] ==  symbol.upper() and i['positionSide'] == side:
                logging.info(f"{symbol} position : {i['positionAmt']}")
                return float(i['positionAmt'])
        return 0.0

    def position(self, symbol, side='LONG'):
        return self._get_positions(symbol, side)

    def position_risk(self, symbol=None):
        """查询指定交易对的持仓风险信息
        
        Args:
            symbol: 交易对符号，如'BTCUSDT'
            
        Returns:
            dict: 包含持仓风险信息的字典，包括：
                - 持仓量
                - 持仓方向
                - 杠杆倍数
                - 未实现盈亏
                - 维持保证金率
                - 强平价格
        """
        positions = self.helper.exchange.fetch_positions(params={'type': 'future'})
        
        if symbol:
            for position in positions:
                if position['symbol'] == symbol.upper():
                    return {
                        'positionAmt': float(position['info']['positionAmt']),
                        'positionSide': position['info']['positionSide'],
                        'leverage': int(position['info']['leverage']),
                        'unRealizedProfit': float(position['info']['unRealizedProfit']),
                        'maintenanceMarginRate': float(position['info']['maintMargin']),
                        'liquidationPrice': float(position['info']['liquidationPrice'])
                    }
        return [p['info'] for p in positions]

    def balance(self):
        balance = self.helper.exchange.fetch_balance(params={'type': 'future'})
        logging.debug(f'balance info: {balance["USDT"]}')
        return balance['USDT']['free'], balance['USDT']['total']

    def get_position_history(self, symbol=None, start_time=None, end_time=None, limit=1000):
        """
        获取历史持仓信息
        
        Args:
            symbol: 交易对符号，如'BTCUSDT'，如果为None则返回所有交易对
            start_time: 开始时间戳（毫秒）
            end_time: 结束时间戳（毫秒）
            limit: 返回结果数量，默认1000，最大1000
            
        Returns:
            list: 历史持仓信息列表，每个元素包含持仓详情
        """
        params = {
            'timestamp': int(time.time() * 1000),
            'limit': min(limit, 1000)  # 确保不超过API限制
        }
        
        if symbol:
            params['symbol'] = symbol.upper()
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time
            
        try:
            # 使用futures API获取历史持仓
            positions = self.helper.exchange.fapiPrivateGetPositionRisk(params)
            return positions
        except Exception as e:
            logging.error(f"获取历史持仓信息失败: {str(e)}")
            raise

class BinanceTradeHelper:
    def __init__(self, symbol, helper, margin_type="ISOLATED", leverage=10):
        self.helper = helper
        self.exchange = self.helper.exchange
        self.acount_helper = BinanceAccountHelper(helper)
        self.acount_helper.change_margin_type(symbol, type=margin_type)
        self.symbol = symbol

        symbol_info = self._get_symbol_info()
        self._price_precision = int(symbol_info['pricePrecision'])
        self._quantity_precision = int(symbol_info['quantityPrecision'])

        # 设置杠杆倍数
        try:
            # l = self.exchange.getLeverage(self.symbol)
            # if l != leverage:
            self.exchange.setLeverage(leverage, self.symbol)
            # logging.info(f"Successfully set leverage to {leverage}x for {self.symbol}")
        except Exception as e:
            # logging.exception(e)
            # logging.error(f"Failed to set leverage for {self.symbol}: {str(e)}")
            pass

        # 设置为双向持仓模式
        try:
            # 检查当前持仓模式
            current_position_mode = self.acount_helper.position_mode(self.symbol)
            # 如果不是双向持仓模式，则切换为双向持仓模式
            if not current_position_mode:
                logging.info(f"Switching to dual position mode for {self.symbol}")
                self.exchange.setPositionMode(True, self.symbol)
                logging.info(f"Successfully switched to dual position mode for {self.symbol}")
        except Exception as e:
            logging.exception(e)
            logging.error(f"Failed to set dual position mode for {self.symbol}: {str(e)}")
        # logging.debug(f'symbol info: {symbol}')

        
        
    def _get_symbol_info(self):
        exchange_info = self.exchange.fapiPublicGetExchangeInfo()
        for s in exchange_info['symbols']:
            if s['symbol'] == self.symbol:
                return s
        raise Exception(f"{self.symbol} not support")
    
    def close_all_positions(self, symbol):
        long_position = self.acount_helper.position(symbol, side='LONG')
        short_position = self.acount_helper.position(symbol, side='SHORT')

        if long_position > 0:
            self.binance_market_sell(long_position, positionSide='LONG')
        
        if short_position > 0:
            self.binance_market_buy(short_position, positionSide='SHORT')
    
    def usdt2amount(self, usdt):
        price = self.helper.fetch_price(self.symbol)
        return round(usdt/price, self._quantity_precision)

    def binance_limit_buy(self, amount, price):
        amount = round(float(amount), self._quantity_precision)
        price  = round(float(price), self._price_precision)
        return self.exchange.create_limit_buy_order(self.symbol, amount, price)

    def binance_limit_sell(self, amount, price):
        amount = round(float(amount), self._quantity_precision)
        price  = round(float(price), self._price_precision)
        return self.exchange.create_limit_sell_order(self.symbol, amount, price)

    def binance_stop_market(self, amount, stopPrice, side='SELL', direct='LONG'):
        amount = round(float(amount), self._quantity_precision)
        stopPrice  = round(float(stopPrice), self._price_precision)
        order = self.exchange.create_order(self.symbol, 'STOP_MARKET', side, amount, None, {'stopPrice': stopPrice, "positionSide": direct})
        return order

    def binance_take_profit_market(self, amount, stopPrice, side='SELL', direct='LONG'):
        amount = round(float(amount), self._quantity_precision)
        stopPrice  = round(float(stopPrice), self._price_precision)
        order = self.exchange.create_order(self.symbol, 'TAKE_PROFIT_MARKET', side, amount, None, {'stopPrice': stopPrice, "positionSide": direct})
        return order

    def binance_stop_limit(self, amount, stopPrice, price, side='SELL', direct='LONG'):
        amount = round(float(amount), self._quantity_precision)
        stopPrice  = round(float(stopPrice), self._price_precision)
        price = round(float(price), self._price_precision)
        order = self.exchange.create_order(self.symbol, 'STOP', side, amount, price, {'stopLossPrice': stopPrice, 'positionSide': direct})
        return order

    def binance_take_profit_limit(self, amount, stopPrice, price, side='SELL', direct='LONG'):
        amount = round(float(amount), self._quantity_precision)
        stopPrice  = round(float(stopPrice), self._price_precision)
        price = round(float(price), self._price_precision)
        order = self.exchange.create_order(self.symbol, 'TAKE_PROFIT', side, amount, price, {'stopPrice': stopPrice, 'positionSide': direct})
        return order

    def binance_market_sell(self, amount, positionSide):
        amount = round(float(amount), self._quantity_precision)
        return self.exchange.create_order(
            symbol=self.symbol,
            type='market',
            side='sell',
            amount=amount,
            params = {"positionSide": positionSide}
        )

    def binance_market_buy(self, amount, positionSide='LONG', margin_type='ISOLATED'):
        amount = round(float(amount), self._quantity_precision)
        return self.exchange.create_market_buy_order(self.symbol, amount, params={
            'positionSide': positionSide,  # 指定方向
            'marginType': margin_type  # 指定仓位模式
        })

    # def binance_create_order(self, quantity, price, stop_price, take_profit_price, side='buy'):
    #     quantity = round(float(quantity), self._quantity_precision)
    #     price  = round(float(price), self._price_precision)
    #     stop_price  = round(float(stop_price), self._price_precision)
    #     take_profit_price  = round(float(take_profit_price), self._price_precision)
    #     return self.exchange.create_order(
    #         symbol=self.symbol,
    #         type='STOP_MARKET',
    #         side=side,
    #         amount=quantity,
    #         # price=price,  # 触发价格
    #         params={
    #             'stopPrice': stop_price,  # 止损价格
    #             # 'price': price,  # 触发价格
    #             'reduceOnly': False,  # 是否仅减仓
    #             'timeInForce': 'GTC',  # 有效期
    #             'newOrderRespType': 'RESULT',  # 响应类型
    #             'activationPrice': price,  # 触发价格
    #             'callbackRate': None,  # 回调率
    #             'workingType': 'MARK_PRICE',  # 工作类型
    #             'orderType': 'TAKE_PROFIT_MARKET',  # 订单类型（止盈市价单）
    #             'stopPrice': take_profit_price,  # 止盈价格
    #         }
    #     )
    
    def binance_create_order(self, quantity, price, stop_price, take_profit_price, side='buy', margin_type='ISOLATED'):
        quantity = round(float(quantity), self._quantity_precision)
        price  = round(float(price), self._price_precision)
        stop_price  = round(float(stop_price), self._price_precision)
        take_profit_price  = round(float(take_profit_price), self._price_precision)
        return self.exchange.create_order(
            symbol=self.symbol,
            type='STOP_MARKET',
            side=side,
            amount=quantity,
            params={
                'stopPrice': stop_price,
                'reduceOnly': False,
                'timeInForce': 'GTC',
                'newOrderRespType': 'RESULT',
                'activationPrice': price,
                'workingType': 'MARK_PRICE',
                'orderType': 'TAKE_PROFIT_MARKET',
                'stopPrice': take_profit_price,
                'positionSide': 'LONG' if side.upper() == 'BUY' else 'SHORT',  # 指定方向
                'marginType': margin_type  # 指定逐仓模式
            }
        )

    def cancel_order(self, oid=None, client_order_id=None):
        if oid:
            logging.info(f'cancel order {oid}')
            return self.exchange.fapiPrivateDeleteOrder({"symbol":self.symbol.upper(), "orderId":oid})
        elif client_order_id:
            logging.info(f'cancel order {client_order_id}')
            return self.exchange.fapiPrivateDeleteOrder({"symbol":self.symbol.upper(), "origClientOrderId":client_order_id})

    def wait_result(self, oid, timeout=5, cancel=True):
        t = 0
        time.sleep(0.1)                                                                                                                                                   
        while True:
            order = self.exchange.fapiPrivateGetOrder({"symbol":self.symbol.upper(), "origClientOrderId":oid['info']['clientOrderId']})
            if order['status'] != "FILLED":
                if t >= timeout:
                    if cancel:
                        try:
                            self.exchange.fapiPrivateDeleteOrder({"symbol":self.symbol.upper(), "origClientOrderId":oid['info']['clientOrderId']})
                        except OrderNotFound as e:
                            continue
                    return StatusCode.FAIL
                t += 1
                time.sleep(0.1)
                continue
            else:
                logging.info(f'------binance {self.symbol.upper()}开单成功-----------')
                return StatusCode.SUCCESS

    def query_orders(self):
        order_book = self.exchange.fetch_open_orders(self.symbol)
        logging.debug(f'open orders: {order_book}')
        return order_book
            

    def place_trailing_stop_market_order(self, symbol, quantity, side='LONG'):
        try:
            # 设置跟踪订单参数
            trailing_params = {
                # 'stopPrice': None,
                'type': 'TRAILING_STOP_MARKET',
                'newOrderRespType': 'RESULT',  # 订单响应类型（完全）
                'quantity': quantity,
                'positionSide': side,
                # 'activationPrice': trail_offset,
                'callbackRate': 0.5
            }
            
            # 下单
            order = self.exchange.create_order(symbol, 'TRAILING_STOP_MARKET', side, quantity, None, trailing_params)
            
            return True
        except Exception as e:
            logging.exception(e)
            logging.error(f'下单失败: {str(e)}')
            return False

    def create_o2o_market_order(self, side, quantity, price, stop_price, take_profit_price):
        quantity = round(float(quantity), self._quantity_precision)
        price  = round(float(price), self._price_precision)
        stop_price  = round(float(stop_price), self._price_precision)
        take_profit_price = round(float(take_profit_price), self._price_precision)

        order = self.exchange.private_post_order_oco({
            'symbol': self.symbol,
            'side': side,  # SELL, BUY
            'quantity': quantity,
            'price': price,
            'stopPrice': stop_price,
            # 'stopLimitPrice': stop_limit_price,  # If provided, stopLimitTimeInForce is required
            # 'stopLimitTimeInForce': 'GTC',  # GTC, FOK, IOC
            # 'listClientOrderId': exchange.uuid(),  # A unique Id for the entire orderList
            # 'limitClientOrderId': exchange.uuid(),  # A unique Id for the limit order
            # 'limitIcebergQty': exchangea.amount_to_precision(symbol, limit_iceberg_quantity),
            # 'stopClientOrderId': exchange.uuid()  # A unique Id for the stop loss/stop loss limit leg
            # 'stopIcebergQty': exchange.amount_to_precision(symbol, stop_iceberg_quantity),
            # 'newOrderRespType': 'ACK',  # ACK, RESULT, FULL
        })

        
        return order