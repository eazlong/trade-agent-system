from collections import namedtuple
import decimal
import math
import threading

from qtcore.private_data_consumer_manager import PrivateDataConsumerManager
from .strategy import Direct, StrategySignalProcessor
from .trade_helper_manager import TradeHelperManager
from .binance_helper import BinanceTradeHelper, BinanceAccountHelper, CommonBinanceHelper
from utils.redis_cache import RedisDict
from abc import abstractmethod
import logging
from .notifier import send_message_to_websocket
from datetime import datetime
from trade.models import TbUserTradeRecord
import json
import time

class QtRobot(StrategySignalProcessor):
    def __init__(self, user, name, trader, config) -> None:
        super().__init__(name)
        self.user = user
        self.config = config
        self.params = json.loads(config.params)
        self.trade_helper = trader #TradeHelperManger().helper(user.id)
        self.status = {}
    
    def destroy(self):
        # Clear robot-related information
        logging.info(f"Cleaning up resources for {self.name}")
        self.status.clear()
        self.params = None
        self.trade_helper = None

    def setConfig(self, config):
        self.config = config
        self.params = json.loads(config.params)

    def do(self, signal):
        if self.config.template_id == signal.template.id:
            if (signal.symbol in self.config.symbol or self.config.symbol == "ALL") and not self.config.paused:
                self.process(signal)
                
    @abstractmethod
    def process(self, signal):
        pass
    
    @abstractmethod
    def _calculate_profit(self, symbol, current_price, amount=0.0):
        pass

    def buy(self, symbol, usdt, side="LONG"):
        b = BinanceTradeHelper(symbol, self.trade_helper, margin_type="CROSSED")
        amount = b.usdt2amount(float(usdt))
        logging.info(f'try to buy {side} : {amount} {symbol}')
        o = b.binance_market_buy(amount, side)
        logging.info(o)
        
        # Save trade record
        TbUserTradeRecord.objects.create(
            user_id=self.user.id,
            bot_id=self.config.id,
            bot_name=self.name,
            symbol=symbol,
            action='buy',
            side=side,
            price=o['price'],
            order_id=o['info']['orderId'],
            amount=amount,
            profit=0.0,
            timestamp = datetime.now())
        return o
    
    def sell(self, symbol, usdt, side="LONG"):
        b = BinanceTradeHelper(symbol, self.trade_helper, margin_type="CROSSED")
        amount = b.usdt2amount(float(usdt))
        logging.info(f'try to sell {side}: {amount} {symbol}')
        
        o = b.binance_market_sell(amount, side)
        logging.info(o)
        
        TbUserTradeRecord.objects.create(
            user_id=self.user.id,
            bot_name=self.name,
            bot_id=self.config.id,
            symbol=symbol,
            action='sell',
            side=side,
            price=o['price'],
            order_id=o['info']['orderId'],
            amount=amount,
            profit=0.0,
            timestamp = datetime.now()
        )
        return o
    
    def close(self, symbol, side='LONG'):
        b = BinanceTradeHelper(symbol, self.trade_helper, margin_type="CROSSED")
        p = BinanceAccountHelper(self.trade_helper).position(symbol, side)
        if p != 0:
            logging.info(f'try to sell {side}: {abs(p)} {symbol}')

            if side == 'LONG':
                o = b.binance_market_sell(abs(p), side)
            else:
                o = b.binance_market_buy(abs(p), side)

            profit, _ = self._calculate_profit(symbol, o['price'], abs(p))
            logging.info(f"Profit from closing position: {profit}")
            # Save trade record
            TbUserTradeRecord.objects.create(
                user_id=self.user.id,
                bot_name=self.name,
                bot_id=self.config.id,
                symbol=symbol,
                action= 'sell' if side == 'LONG' else 'buy',
                price=o['price'],
                order_id=o['info']['orderId'],
                amount=abs(p),
                side=side,
                profit=profit,
                timestamp = datetime.now()
            )
            return o
        
    def close_all(self, symbol):
        b = BinanceTradeHelper(symbol, self.trade_helper, margin_type="CROSSED")
        b.close_all_positions(symbol=symbol)
        

from .i_private_data_consumer import PrivateDataConsumer
from .sl_tp_method import MovingStopLoss

class CommonBot(QtRobot, PrivateDataConsumer):
    class Status:
        def __init__(self):
            self.entry_price = 0.0
            self.entry_position = 0.0
            self.take_profit_order_id_1 = None  # 1:1 盈亏比止盈1/3
            self.take_profit_order_id_2 = None  # 移动止盈止损订单
            self.stop_loss_order_id = None      # 初始止损订单
            self.entry_time = 0
            self.remaining_position = 0.0       # 剩余仓位

        def __str__(self) -> str:
            return f"{self.entry_price} - {self.entry_position} - {self.remaining_position}"

    def __init__(self, user, trader, config) -> None:
        QtRobot.__init__(self, user, 'Common', trader, config)
        PrivateDataConsumer.__init__(self, user.id)
        PrivateDataConsumerManager().register(user.id, self)
        self.status = RedisDict(f'CommonBot_{self.config.id}_user_{user.id}', {})
        self._status_lock = threading.RLock()

        # 获取移动止损管理器实例
        self.moving_stop_loss_manager = MovingStopLoss()
        

    def process(self, signal):
        with self._status_lock:
            # if len(self.status.keys()) >= 5:
            #     logging.warning(f"CommonBot process {signal.symbol} - {len(self.status.keys())} >= 5, return")
            #     return

            if signal.symbol in self.status:
                s = self.status[signal.symbol]
                # if time.time() - s.entry_time < self.params['entry_duration']:
                logging.warning(f"CommonBot process {signal.symbol} - {time.time() - s.entry_time} < {self.params['entry_duration']}")
                return

            if signal.direct == Direct.LONG:
                logging.info(f"CommonBot process {signal.symbol} - {len(self.status.keys())}")
                if signal.params['rise'] > 0.05:
                    logging.warning(f"CommonBot process {signal.symbol} - rise is 5%, return")
                    return

                o = self.buy(signal.symbol, self.config.usdt, "LONG")
                helper = BinanceTradeHelper(signal.symbol, self.trade_helper, margin_type="CROSSED")
                price = o['price']
                amount = o['amount']

                # 三部分止盈策略
                # 1. 1:1 盈亏比止盈1/2仓位
                take_profit_1_amount = amount / 2
                if 'stop_loss_pct' in self.params:
                    take_profit_price_1 = price * (1 + float(self.params['stop_loss_pct']))
                else:
                    pct = (price - signal.params['sl'])/price
                    if pct > 0.03:
                        pct = 0.03
                        
                    take_profit_price_1 = price * (1 + pct)
                take_profit_price_2 = price * (1 + float(self.params['take_profit_pct']))

                # 2. 移动止盈止损剩余1/2仓位
                take_profit_2_amount = amount - take_profit_1_amount

                # 3. 初始止损
                stop_loss_price = price * (1 - float(self.params['stop_loss_pct'])) if 'stop_loss_pct' in self.params else signal.params['sl']

                logging.info(f"三部分止盈策略 - 1:1止盈: {take_profit_price_1:.4f} ({take_profit_1_amount:.4f}), 移动止盈: ({take_profit_2_amount:.4f}), 初始止损: {stop_loss_price:.4f}")

                # 创建订单
                tsl = helper.binance_stop_market(amount, stop_loss_price)
                tpl_1 = helper.binance_take_profit_limit(take_profit_1_amount, take_profit_price_1, take_profit_price_1)
                tpl_2 = helper.binance_take_profit_limit(take_profit_2_amount, take_profit_price_2, take_profit_price_2)

                s = self.Status()
                s.entry_price = float(o['price'])
                s.entry_position = float(o['amount'])
                s.remaining_position = take_profit_2_amount
                s.take_profit_order_id_1 = tpl_1['info']['clientOrderId']
                s.take_profit_order_id_2 = tpl_2['info']['clientOrderId']
                s.stop_loss_order_id = tsl['info']['clientOrderId']
                s.entry_time = time.time()
                self.status[signal.symbol] = s

    def private_data_process(self, data):
        with self._status_lock:
            symbol = data['s']
            if symbol not in self.status:
                return

            try:
                s = self.status[symbol]
                # 处理订单成交
                if data['X'] == 'FILLED':
                    logging.info(f"{symbol} private_data_process {data}")
                    helper = BinanceTradeHelper(symbol, self.trade_helper, margin_type="CROSSED")

                    user_positions = self.moving_stop_loss_manager.get_position_info(self.user.id, symbol)
                    if user_positions:
                        logging.info(f"{symbol} 移动止损触发 {data['c']} - {user_positions.stop_loss_order_id}")
                        if user_positions.stop_loss_order_id == data['c']:
                            del self.status[symbol]

                            self._cleanup_orders(symbol, s, helper)
                            # 取消移动止损注册
                            self.moving_stop_loss_manager.unregister(self.user.id, symbol)

                    if data['c'] == s.take_profit_order_id_1 or data['c'] == s.take_profit_order_id_2 or data['c'] == s.stop_loss_order_id:
                        logging.info(f"{symbol} private_data_process {data['c']} - {s.take_profit_order_id_1} - {s.take_profit_order_id_2} - {s.stop_loss_order_id}")
                        profit = self._calculate_profit(symbol, float(data['p']), float(data['q']))
                        TbUserTradeRecord.objects.create(
                            user_id=self.user.id,
                            bot_name=self.name,
                            bot_id=self.config.id,
                            symbol=symbol,
                            action= 'sell',
                            price=float(data['p']),
                            order_id=str(data['i']),
                            amount=float(data['q']),
                            side=data['ps'],
                            profit=0.0,
                            timestamp = datetime.now()
                        )

                        # 如果是1:1止盈订单成交，注册移动止损
                        if data['c'] == s.take_profit_order_id_1:
                            logging.info(f"{symbol} 1:1止盈订单成交，注册移动止损")
                            self._register_moving_stop_loss(symbol, s)

                        # 如果是移动止盈止损订单或止损订单成交，清空仓位
                        elif data['c'] == s.take_profit_order_id_2 or data['c'] == s.stop_loss_order_id:
                            logging.info(f"{symbol} 移动止损订单或止盈订单成交，清空仓位")
                            self._cleanup_orders(symbol, s, helper)
                            # 取消移动止损注册
                            self.moving_stop_loss_manager.unregister(self.user.id, symbol)
                            del self.status[symbol]

            except Exception as e:
                logging.exception(e)

    def _register_moving_stop_loss(self, symbol, status):
        """注册移动止损管理"""
        try:
            # 使用当前价格作为移动止损的起点
            current_price = status.entry_price * 1.01  # 假设价格已经上涨1%

            # 注册到移动止损管理器
            success = self.moving_stop_loss_manager.register(
                user_id=self.user.id,
                symbol=symbol,
                amount=status.remaining_position,
                entry_time=status.entry_time,
                lookback_period=10,
                is_long=True
            )

            if success:
                logging.info(f"成功注册移动止损管理: {symbol}, 数量: {status.remaining_position:.4f}")
            else:
                logging.error(f"注册移动止损管理失败: {symbol}")

        except Exception as e:
            logging.error(f"注册移动止损失败: {e}")

    def _cleanup_orders(self, symbol, status, helper):
        """清理所有订单"""
        try:
            helper.cancel_order(client_order_id=status.take_profit_order_id_1)
        except:
            pass
        try:
            helper.cancel_order(client_order_id=status.take_profit_order_id_2)
        except:
            pass
        try:
            helper.cancel_order(client_order_id=status.stop_loss_order_id)
        except:
            pass

    def _calculate_profit(self, symbol, cur_price, amount):
        s = self.status[symbol]
        return amount * cur_price - s.entry_price * amount
         
class DCABot(QtRobot):
    class Status:
        def __init__(self):
            self.entry_price = []
            self.entry_position = []
            self.position_count = 0

        def __str__(self) -> str:
            return f"{self.entry_price} - {self.entry_position} - {self.position_count}"

    def __init__(self, user, name, trader, side, config) -> None:
        super().__init__(user, name, trader, config)
        self.side = side
        self.status = RedisDict(f'{name}Bot_{self.config.id}_user_{user.id}', {})

        if self.params['immediate_entry']:
            if self.config.symbol not in self.status:
               self.entry(self.config.symbol)

    def log(self, msg):
        logging.info(msg)
        send_message_to_websocket(self.user.username, msg)

    def _calculate_profit(self, symbol, current_price):
        if symbol not in self.status:
            return 0.0
        
        s = self.status[symbol]
        total_cost = sum(s.entry_position)
        total_value = 0
        p = s.entry_position
        for i, v in enumerate(s.entry_price):
            total_value += p[i] / v * current_price

        profit = total_cost - total_value  if self.side == "SHORT" else total_value - total_cost

        logging.info(f"{self.__class__.__name__} Current profit for {symbol}: {profit}")
        return profit, total_cost

    def entry(self, symbol):
        side = self.side
        _, total = BinanceAccountHelper(self.trade_helper).balance()
        usdt = total * self.params['initial_allocation_pct']
        o = self.sell(symbol, usdt, side=side) if side == "SHORT" else self.buy(symbol, usdt=usdt)
        s = self.Status()
        s.entry_price.append(o['price'])
        s.entry_position.append(o['cost'])
        s.position_count = 1
        self.status[symbol] = s
        self.log(f"{self.__class__.__name__} Entered {symbol} {side} at {o['price']}, Size: {usdt}")

    def add(self, symbol, add_pct=0.2):
        side = self.side
        s = self.status[symbol]
        usdt = sum(s.entry_position) * add_pct
        o = self.sell(symbol, usdt=usdt, side="SHORT")  if side == "SHORT" else self.buy(symbol, usdt=usdt)
        s.entry_price.append(o['price'])
        s.entry_position.append(o['cost'])
        s.position_count += 1
        self.status[symbol] = s
        self.log(f"{self.__class__.__name__} {s.position_count} times Added {symbol} {side} at {o['price']}, Size: {usdt}")

    def leave(self, symbol):
        o = self.close(symbol, self.side)
        self.log(f"{self.__class__.__name__}  Exited {symbol} {self.side} at {o['price']}")
        del self.status[symbol]
    
    def destroy(self):
        # Clear robot-related information
        for symbol in self.status:
            self.close(symbol, self.side)

        super().destroy()

class HighWinRateBot(DCABot):
    SIDE = "LONG"
    def __init__(self, user, trader, config) -> None:
        super().__init__(user, 'HighWinRate', trader, self.SIDE, config)

    def process(self, signal):
        if signal.direct == Direct.LONG:
            if signal.symbol not in self.status and signal.interval=="15m":
                self.entry(signal.symbol)
            elif signal.symbol in self.status and signal.interval=='1m':
                s = self.status[signal.symbol]
                if (signal.data['close'][-1] < s.entry_price[-1] * (1 - self.params['min_down_pct']) and
                    s.position_count <= self.params['max_additions']):
                    self.add(signal.symbol, self.params['add_pct'])

        elif signal.direct == Direct.SHORT:
            if signal.symbol in self.status and signal.interval=="15m":
                s = self.status[signal.symbol]
                logging.info(f'{self.__class__.__name__} last entry status {s.__dict__} for {signal.symbol}')
                p, t = self._calculate_profit(signal.symbol, signal.data['close'][-1])
                if p / t > self.params['profit_pct']:
                    self.leave(signal.symbol)
                    if self.params['immediate_entry']:
                        self.entry(signal.symbol)

                elif self.params['do_loss_stop'] and signal.data['close'][-1] < s.entry_price[-1] and len(s.entry_price) == self.params['max_additions']:
                    self.leave(signal.symbol)
                    if self.params['immediate_entry']:
                        self.entry(signal.symbol)

class HighWinRateShortBot(DCABot):
    SIDE = "SHORT"
    def __init__(self, user, trader, config) -> None:
        super().__init__(user, 'HighWinRateShort', trader, self.SIDE, config)

    def process(self, signal):
        if signal.direct == Direct.SHORT:
            if signal.symbol not in self.status and signal.interval=="15m":
               self.entry(signal.symbol)
            elif signal.symbol in self.status and signal.interval=='1m':
                s = self.status[signal.symbol]
                if (signal.data['close'][-1] > s.entry_price[-1] * (1 + self.params['min_down_pct']) and
                    s.position_count < self.params['max_additions']):
                    self.add(signal.symbol, self.params['add_pct'])

        elif signal.direct == Direct.LONG:
            if signal.symbol in self.status and signal.interval=="15m":
                s = self.status[signal.symbol]
                logging.info(f'{self.__class__.__name__} last entry status {s.__dict__} for {signal.symbol}')
                p, t = self._calculate_profit(signal.symbol, signal.data['close'][-1])
                if p / t > self.params['profit_pct']:
                    self.leave(signal.symbol)
                    if self.params['immediate_entry']:
                        self.entry(signal.symbol)
                elif self.params['do_loss_stop'] and signal.data['close'][-1] > s.entry_price[-1] and len(s.entry_price) == self.params['max_additions']:
                    self.leave(signal.symbol)
                    if self.params['immediate_entry']:
                        self.entry(signal.symbol)

