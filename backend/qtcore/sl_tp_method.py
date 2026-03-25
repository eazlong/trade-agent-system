from typing import List, Dict, Optional, Tuple
from collections import defaultdict, deque
import logging
import threading
import ccxt
from qtcore.binance_helper import BinanceTradeHelper
from .i_wss_data_consumer import WSSDataConsumer
from .trade_helper_manager import TradeHelperManager
from utils.singleton import singleton

class PositionInfo:
    """持仓信息类"""
    def __init__(self, user_id: int, symbol: str, amount: float, entry_time: int, 
                 lookback_period: int = 20, is_long: bool = True):
        self.user_id = user_id
        self.symbol = symbol
        self.amount = amount
        self.pre_entry_time = None
        self.entry_time = entry_time * 1000
        self.lookback_period = lookback_period
        self.is_long = is_long

        # 移动止损相关
        self.current_stop_loss = None
        
        # 订单管理
        self.stop_loss_order_id = None
        
        # 状态跟踪
        self.is_active = True
        
        
    def calculate_moving_stop_loss(self, data: dict) -> float:
        """计算移动止损价格"""
      
        # entry_time = self.pre_entry_time if self.pre_entry_time != None else self.entry_time
        entry_time = self.entry_time
        index = next((i for i, x in enumerate(data['timestamp']) if x <= entry_time and (i == len(data['timestamp']) - 1 or data['timestamp'][i+1] > entry_time)), None)
        logging.debug(f"{self.symbol}, entry_time: {entry_time}, index: {index}")
        if index is None or index > len(data['timestamp']) - 4:
            return self.current_stop_loss
        
        current_high = max(data['high'][index:-1])
        current_low = min(data['low'][index+1:-1])
        logging.debug(f"{self.symbol}, 前高: {current_high:.4f}, 前低: {current_low:.4f}, 当前价格: {data['close'][-1]:.4f}")
        
        current_price = data['close'][-1]
        volume = data['volume'][-1]
        pre_volume = data['volume'][-2]
        # 当价格突破前高时，将止损移动到前一个相对低点
        new_stop_loss = None
        if current_price > current_high and volume > pre_volume * 1.5:
            new_stop_loss = current_low * 0.995
            self.pre_entry_time = self.entry_time
            self.entry_time = data['timestamp'][-1]
            logging.info(f"移动止损更新: {self.symbol}, 前高: {current_high:.4f}, "
                       f"前低: {current_low:.4f}, 新止损: {new_stop_loss:.4f}")
            
        return new_stop_loss
        

@singleton
class MovingStopLoss(WSSDataConsumer):
    """移动止损止盈管理器"""
    
    def __init__(self):
        super().__init__("MovingStopLoss", ['1m'], [])
        self.helper = TradeHelperManager()
        
        # 用户持仓管理: {user_id: {symbol: PositionInfo}}
        self.user_positions: Dict[int, Dict[str, PositionInfo]] = defaultdict(dict)
        
        # 活跃交易对列表
        self.active_symbols = set()
        self.lock = threading.Lock()

        logging.info(f"MovingStopLoss for {self.name} initialized with batch interval {self.interval}s.")

        
    def need_process(self, interval: str, symbol: str) -> bool:
        """判断是否需要处理该交易对的数据"""
        with self.lock:
            logging.debug(f"判断是否需要处理该交易对的数据: {interval}, {symbol}, {self.interval}, {self.active_symbols}")
            return interval in self.interval and symbol in self.active_symbols
    
    def process(self, symbol: str, data: dict, interval: str):
        """处理 WebSocket 数据"""
        if not self.need_process(interval, symbol):
            return
            
        try:    
            # 更新所有用户的该交易对持仓
            with self.lock:
                for user_id, positions in self.user_positions.items():
                    if symbol in positions:
                        position = positions[symbol]
                        if position.is_active:
                            self._process_position(position, data, symbol)
                        
        except Exception as e:
            logging.error(f"处理 {symbol} 数据时出错: {str(e)}") 
            
    def _process_position(self, position: PositionInfo, data: dict, symbol: str):
        """处理单个持仓的移动止损逻辑"""
        try:
            # 计算新的止损价格
            new_stop_loss = position.calculate_moving_stop_loss(data)
            if new_stop_loss == None:
                return
            # 更新止损订单
            self._update_stop_loss_order(position, new_stop_loss)
                
        except Exception as e:
            logging.error(f"处理持仓 {symbol} 时出错: {str(e)}")
            
    def register(self, user_id: int, symbol: str, amount: float, 
                entry_time: int, lookback_period: int = 20, 
                is_long: bool = True) -> bool:
        """
        注册用户持仓进行移动止损管理
        
        Args:
            user_id: 用户ID
            symbol: 交易对
            amount: 仓位数量
            initial_stop_loss: 初始止损价格
            lookback_period: 回看周期
            is_long: 是否是多头仓位
            
        Returns:
            bool: 注册是否成功
        """
        try:
            # 检查用户是否有交易权限
            helper = self.helper.helper(user_id)
            if not helper:
                logging.error(f"用户 {user_id} 没有有效的交易助手")
                return False
                
            # 创建持仓信息
            position = PositionInfo(
                user_id=user_id,
                symbol=symbol,
                amount=amount,
                entry_time=entry_time,
                lookback_period=lookback_period,
                is_long=is_long
            )
            
            # 注册到管理器中
            with self.lock:
                self.user_positions[user_id][symbol] = position
                self.active_symbols.add(symbol)
            
            # 创建初始止损订单
            # self._create_initial_orders(position)
            from datetime import datetime
            logging.info(f"成功注册移动止损: 用户 {user_id}, 交易对 {symbol}, "
                       f"数量 {amount}, 开仓时间 {datetime.fromtimestamp(entry_time)}")
            return True
            
        except Exception as e:
            logging.error(f"注册移动止损失败: {str(e)}")
            return False
            
    def unregister(self, user_id: int, symbol: str) -> bool:
        """取消注册移动止损"""
        try:
            with self.lock:
                if user_id in self.user_positions and symbol in self.user_positions[user_id]:
                    position = self.user_positions[user_id][symbol]
                    
                    # 取消订单
                    self._cancel_orders(position)
                
                # 移除管理
                del self.user_positions[user_id][symbol]
                if not self.user_positions[user_id]:
                    del self.user_positions[user_id]
                    
                # 更新活跃交易对列表
                self._update_active_symbols()
                
                logging.info(f"取消注册移动止损: 用户 {user_id}, 交易对 {symbol}")
                return True
                
        except Exception as e:
            logging.error(f"取消注册移动止损失败: {str(e)}")
            
        return False
        
    def _create_initial_orders(self, position: PositionInfo):
        """创建初始止损止盈订单"""
        try:
            helper = self.helper.helper(position.user_id)
            if not helper:
                return
            trader = BinanceTradeHelper(position.symbol, helper)
            # 创建止损订单
            stop_loss_order = trader.binance_stop_market(
                symbol=position.symbol,
                side='sell' if position.is_long else 'buy',
                amount=position.amount,
                params={
                    'stopPrice': position.initial_stop_loss,
                    'reduceOnly': True
                }
            )
            
            position.stop_loss_order_id = stop_loss_order['info']['clientOrderId']
            logging.info(f"创建止损订单: {position.symbol}, 订单ID: {position.stop_loss_order_id}")
            
        except Exception as e:
            logging.error(f"创建初始订单失败: {str(e)}")
            
    def _update_stop_loss_order(self, position: PositionInfo, new_stop_loss: float):
        try:
            helper = self.helper.helper(position.user_id)
            if not helper:
                return

            trader = BinanceTradeHelper(position.symbol, helper)
            # 如果止损价格有变化，更新订单
            if not position.current_stop_loss or abs(new_stop_loss - position.current_stop_loss)/position.current_stop_loss > 0.001:
                # 取消旧订单
                if position.stop_loss_order_id:
                    try:
                        trader.cancel_order(client_order_id=position.stop_loss_order_id)
                    except ccxt.OrderNotFound:
                        pass  # 订单可能已经被执行
                
                # 创建新订单
                new_order = trader.binance_stop_market(
                    amount=position.amount,
                    stopPrice=new_stop_loss,
                    side='sell' if position.is_long else 'buy',
                    direct='LONG' if position.is_long else 'SHORT'
                )
                
                position.stop_loss_order_id = new_order['info']['clientOrderId']
                position.current_stop_loss = new_stop_loss
                
                logging.info(f"更新止损订单: {position.symbol}, 新止损: {new_stop_loss:.4f}, 订单ID: {position.stop_loss_order_id}")
                
        except Exception as e:
            logging.error(f"更新止损订单失败: {str(e)}")
            
    def _close_position(self, position: PositionInfo, current_price: float):
        """平仓操作"""
        try:
            helper = self.helper.helper(position.user_id)
            if not helper:
                return
            trader = BinanceTradeHelper(position.symbol, helper)    
            # 创建市价平仓订单
            close_order = trader.binance_market_sell(
                amount=position.amount,
                positionSide='LONG' if position.is_long else 'SHORT'
            )
            
            # 标记持仓为非活跃
            position.is_active = False
            
            logging.info(f"平仓成功: {position.symbol}, 订单ID: {close_order['id']}, "
                       f"价格: {current_price:.4f}")
            
            # 取消注册
            self.unregister(position.user_id, position.symbol)
            
        except Exception as e:
            logging.error(f"平仓失败: {str(e)}")
            
    def _cancel_orders(self, position: PositionInfo):
        """取消所有相关订单"""

        try:
            helper = self.helper.helper(position.user_id)
            if not helper:
                return
            trader = BinanceTradeHelper(position.symbol, helper)    
            # 取消止损订单
            if position.stop_loss_order_id:
                try:
                    trader.cancel_order(client_order_id=position.stop_loss_order_id)
                except ccxt.OrderNotFound:
                    pass 
                    
        except Exception as e:
            logging.error(f"取消订单失败: {str(e)}")
            
    def _update_active_symbols(self):
        """更新活跃交易对列表"""
        active_symbols = set()
        for user_positions in self.user_positions.values():
            for symbol, position in user_positions.items():
                if position.is_active:
                    active_symbols.add(symbol)
        self.active_symbols = active_symbols
        
    def get_position_info(self, user_id: int, symbol: str) -> Optional[PositionInfo]:
        """获取持仓信息"""
        if user_id in self.user_positions and symbol in self.user_positions[user_id]:
            return self.user_positions[user_id][symbol]
        return None
        
    def get_user_positions(self, user_id: int) -> Dict[str, PositionInfo]:
        """获取用户所有持仓"""
        return self.user_positions.get(user_id, {})
        
    def get_all_active_positions(self) -> List[PositionInfo]:
        """获取所有活跃持仓"""
        positions = []
        for user_positions in self.user_positions.values():
            for position in user_positions.values():
                if position.is_active:
                    positions.append(position)
        return positions
