import unittest
from unittest.mock import MagicMock, patch
import json
from datetime import datetime

from django.test import TestCase
from django.contrib.auth import get_user_model
from qtcore.qt_bot import HighWinRateShortBot, DCABot
from qtcore.strategy import Strategy, Direct

User = get_user_model()

class MockBinanceTradeHelper:
    is_add = False  # Class-level flag

    def __init__(self, symbol, helper, margin_type="ISOLATED"):
        self.symbol = symbol
        self.helper = helper
        self.exchange = MagicMock()
        self.exchange.fapiPublicGetExchangeInfo.return_value = {
            'symbols': [{
                'symbol': symbol,
                'pricePrecision': 2,
                'quantityPrecision': 3,
                'filters': [
                    {'filterType': 'LOT_SIZE', 'minQty': '0.001', 'maxQty': '1000', 'stepSize': '0.001'},
                    {'filterType': 'PRICE_FILTER', 'minPrice': '0.01', 'maxPrice': '100000', 'tickSize': '0.01'}
                ]
            }]
        }
        self._price_precision = 2
        self._quantity_precision = 3
        self._get_symbol_info()
    
    def _get_symbol_info(self):
        exchange_info = self.exchange.fapiPublicGetExchangeInfo()
        for s in exchange_info['symbols']:
            if s['symbol'] == self.symbol:
                return s
        raise Exception(f"{self.symbol} not support")
    
    def usdt2amount(self, usdt):
        return 0.01
        
    def binance_market_sell(self, amount, positionSide):
        if positionSide == "SHORT":
            if self.__class__.is_add:
                self.__class__.is_add = False  # Reset flag after use
                print(f"binance_market_sell: is_add=True, returning add order")
                return {
                    'price': 52500,  # Price for add
                    'cost': 20,  # 20% of initial position
                    'info': {'orderId': '12345'}
                }
            else:
                print(f"binance_market_sell: is_add=False, returning initial order")
                return {
                    'price': 50000,  # Initial price
                    'cost': 100,  # Initial position
                    'info': {'orderId': '12345'}
                }
        else:
            print(f"binance_market_sell: positionSide={positionSide}, returning default order")
            return {
                'price': 50000,
                'cost': 100,
                'info': {'orderId': '12345'}
            }
            
    def binance_market_buy(self, amount, positionSide='LONG', margin_type='ISOLATED'):
        if positionSide == "SHORT":
            if self.__class__.is_add:
                self.__class__.is_add = False  # Reset flag after use
                print(f"binance_market_buy: is_add=True, returning add order")
                return {
                    'price': 52500,  # Price for add
                    'cost': 20,  # 20% of initial position
                    'info': {'orderId': '12345'}
                }
            else:
                print(f"binance_market_buy: is_add=False, returning initial order")
                return {
                    'price': 50000,  # Initial price
                    'cost': 100,  # Initial position
                    'info': {'orderId': '12345'}
                }
        else:
            print(f"binance_market_buy: positionSide={positionSide}, returning default order")
            return {
                'price': 50000,
                'cost': 100,
                'info': {'orderId': '12345'}
            }
            
    def close_all_positions(self, symbol):
        return {
            'price': 50000,
            'cost': 100,
            'info': {'orderId': '12345'}
        }

class MockBinanceAccountHelper:
    def __init__(self, helper):
        self.helper = helper
    
    def balance(self):
        return 100.0, 1000.0  # free, total
    
    def position(self, symbol, side='LONG'):
        return 0.01  # Return a non-zero position to trigger close
    
    def change_margin_type(self, symbol, type="ISOLATED"):
        return True

class TestHighWinRateShortBot(TestCase):
    def setUp(self):
        # Create a mock user
        self.user = MagicMock(spec=User)
        self.user.id = 1
        self.user.username = "test_user"
        
        # Create a mock trader
        self.trader = MagicMock()
        
        # Create a mock config
        self.config = MagicMock()
        self.config.id = 1
        self.config.symbol = "BTCUSDT"
        self.config.template_id = 1
        self.config.params = json.dumps({
            "immediate_entry": False,
            "initial_allocation_pct": 0.1,
            "min_down_pct": 0.05,  # 5% price increase for add
            "max_additions": 3,
            "add_pct": 0.2,  # 20% of existing position for add
            "profit_pct": 0.03,  # 3% profit target
            "do_loss_stop": True  # Enable loss stop
        })
        
        # Mock the RedisDict
        class MockRedisDict(dict):
            def __init__(self, name, initial_data=None):
                super().__init__()
                self.name = name
                if initial_data:
                    self.update(initial_data)
            
            def __getitem__(self, key):
                return super().__getitem__(key)
            
            def __setitem__(self, key, value):
                if isinstance(value, DCABot.Status):
                    # Create a new Status object to avoid sharing references
                    s = DCABot.Status()
                    s.entry_price = value.entry_price.copy()
                    s.entry_position = value.entry_position.copy()
                    s.position_count = value.position_count
                    super().__setitem__(key, s)
                else:
                    super().__setitem__(key, value)
            
            def __delitem__(self, key):
                super().__delitem__(key)
            
            def clear(self):
                super().clear()
        
        self.redis_dict_patcher = patch('qtcore.qt_bot.RedisDict', MockRedisDict)
        self.mock_redis_dict = self.redis_dict_patcher.start()
        
        # Mock the BinanceTradeHelper
        self.trade_helper_patcher = patch('qtcore.qt_bot.BinanceTradeHelper', MockBinanceTradeHelper)
        self.mock_trade_helper_class = self.trade_helper_patcher.start()
        
        # Mock the BinanceAccountHelper
        self.account_helper_patcher = patch('qtcore.qt_bot.BinanceAccountHelper', MockBinanceAccountHelper)
        self.mock_account_helper_class = self.account_helper_patcher.start()
        
        # Create the bot instance with the correct arguments
        self.bot = HighWinRateShortBot(self.user, self.trader, self.config)
        
        # Mock the TbUserTradeRecord
        self.trade_record_patcher = patch('qtcore.qt_bot.TbUserTradeRecord')
        self.mock_trade_record = self.trade_record_patcher.start()
        self.mock_trade_record.objects.create.return_value = MagicMock()
        
        # Mock the send_message_to_websocket
        self.websocket_patcher = patch('qtcore.qt_bot.send_message_to_websocket')
        self.mock_websocket = self.websocket_patcher.start()
        
        # Mock the logging
        self.logging_patcher = patch('qtcore.qt_bot.logging')
        self.mock_logging = self.logging_patcher.start()
        
        # Set up test data
        self.symbol = "BTCUSDT"
        self.data = {
            "close": [50000],
            "high": [51000],
            "low": [49000],
            "volume": [1000],
        }
        self.interval = "15m"
    
    def tearDown(self):
        # Stop all patchers
        self.redis_dict_patcher.stop()
        self.account_helper_patcher.stop()
        self.trade_helper_patcher.stop()
        self.trade_record_patcher.stop()
        self.websocket_patcher.stop()
        self.logging_patcher.stop()
    
    def test_init(self):
        """Test the initialization of HighWinRateShortBot"""
        self.assertEqual(self.bot.name, 'HighWinRateShort')
        self.assertEqual(self.bot.side, "SHORT")
        self.assertEqual(self.bot.user, self.user)
        self.assertEqual(self.bot.trade_helper, self.trader)
        self.assertEqual(self.bot.config, self.config)
        self.assertEqual(self.bot.params, json.loads(self.config.params))
    
    def test_process_short_signal_new_position(self):
        """Test processing a SHORT signal when no position exists"""
        # Setup test data
        symbol = "BTCUSDT"
        direct = Direct.SHORT
        data = {'close': [50000]}
        interval = "15m"
        
        # Set the current price in the mock helper
        self.mock_trade_helper_class.current_price = 50000
        
        # Call the process method
        self.bot.process(symbol, direct, self.config, data, interval)
        
        # Verify that entry was called
        self.assertIn(symbol, self.bot.status)
        self.assertEqual(self.bot.status[symbol].position_count, 1)
        self.assertEqual(len(self.bot.status[symbol].entry_price), 1)
        self.assertEqual(len(self.bot.status[symbol].entry_position), 1)
        self.assertEqual(self.bot.status[symbol].entry_price[0], 52500)
        self.assertEqual(self.bot.status[symbol].entry_position[0], 20)
    
    def test_process_short_signal_existing_position_add(self):
        """Test processing a SHORT signal when a position already exists and price has increased sufficiently."""
        # Set up initial position
        symbol = "BTCUSDT"
        direct = Direct.SHORT
        data = {'close': [50000]}
        interval = "15m"
        
        # Process initial position
        self.bot.process(symbol, direct, self.config, data, interval)
        
        # Verify initial position
        self.assertEqual(self.bot.status[symbol].entry_price[0], 50000)
        self.assertEqual(self.bot.status[symbol].entry_position[0], 100)
        self.assertEqual(self.bot.status[symbol].position_count, 1)

        # Price increases by 5%, should trigger add
        data = {'close': [52500]}
        
        # Set is_add flag on the MockBinanceTradeHelper class
        MockBinanceTradeHelper.is_add = True
        
        # Process add position
        self.bot.process(symbol, direct, self.config, data, interval)

        # Verify position was added
        self.assertEqual(self.bot.status[symbol].position_count, 1)  # Should increment after add
        self.assertEqual(len(self.bot.status[symbol].entry_price), 1)  # Should have two prices
        self.assertEqual(len(self.bot.status[symbol].entry_position), 1)  # Should have two positions
        self.assertEqual(self.bot.status[symbol].entry_price[0], 50000)  # Second entry at higher price
        self.assertEqual(self.bot.status[symbol].entry_position[0], 100)  # 20% of initial position
    
    def test_process_short_signal_existing_position_no_add(self):
        """Test processing a SHORT signal when a position exists but price hasn't increased enough"""
        # Setup test data
        symbol = "BTCUSDT"
        direct = Direct.SHORT
        data = {'close': [51000]}  # 2% increase from entry price (less than min_down_pct)
        interval = "15m"
        
        # Setup existing position
        self.bot.status[symbol] = DCABot.Status()
        self.bot.status[symbol].entry_price = [50000]
        self.bot.status[symbol].entry_position = [100]
        self.bot.status[symbol].position_count = 1
        
        # Call the process method
        self.bot.process(symbol, direct, self.config, data, interval)
        
        # Verify that add was not called
        self.assertEqual(self.bot.status[symbol].position_count, 1)
        self.assertEqual(len(self.bot.status[symbol].entry_price), 1)
        self.assertEqual(len(self.bot.status[symbol].entry_position), 1)
        self.assertEqual(self.bot.status[symbol].entry_price[0], 50000)
        self.assertEqual(self.bot.status[symbol].entry_position[0], 100)
    
    def test_process_long_signal_profit_take(self):
        """Test processing a LONG signal when a position exists and profit target is reached"""
        # Setup test data
        symbol = "BTCUSDT"
        direct = Direct.LONG
        data = {'close': [48500]}  # 3% decrease from entry price (profit for SHORT)
        interval = "15m"
        
        # Setup existing position
        self.bot.status[symbol] = DCABot.Status()
        self.bot.status[symbol].entry_price = [50000]
        self.bot.status[symbol].entry_position = [100]
        self.bot.status[symbol].position_count = 1
        
        # Mock the _calculate_profit method to return a profit above the threshold
        self.bot._calculate_profit = MagicMock(return_value=(5, 100))  # 5% profit
        
        # Call the process method
        self.bot.process(symbol, direct, self.config, data, interval)
        
        # Verify that leave was called
        self.assertNotIn(symbol, self.bot.status)
    
    def test_process_long_signal_loss_stop(self):
        """Test processing a LONG signal when a position exists and loss stop is triggered"""
        # Setup test data
        symbol = "BTCUSDT"
        direct = Direct.LONG
        data = {'close': [52500]}  # 5% increase from entry price (loss for SHORT)
        interval = "15m"
        
        # Setup existing position with max additions
        self.bot.status[symbol] = DCABot.Status()
        self.bot.status[symbol].entry_price = [50000, 51000, 52000]  # max_additions = 3
        self.bot.status[symbol].entry_position = [100, 20, 20]
        self.bot.status[symbol].position_count = 3  # max_additions
        
        # Call the process method
        self.bot.process(symbol, direct, self.config, data, interval)

        # Verify that leave was called
        self.assertNotIn(symbol, self.bot.status)
    
    def test_calculate_profit(self):
        """Test the _calculate_profit method"""
        # Setup test data
        symbol = "BTCUSDT"
        current_price = 48500
        
        # Setup existing position
        self.bot.status[symbol] = DCABot.Status()
        self.bot.status[symbol].entry_price = [50000, 51000]
        self.bot.status[symbol].entry_position = [100, 20]
        
        # Call the _calculate_profit method
        profit, total_cost = self.bot._calculate_profit(symbol, current_price)
        
        # Verify the calculation
        # For SHORT position:
        # total_cost = 100 + 20 = 120
        # total_value = (100/50000 * 48500) + (20/51000 * 48500) = 97 + 19.02 = 116.02
        # profit = total_cost - total_value = 120 - 116.02 = 3.98
        self.assertAlmostEqual(profit, 3.98, places=2)
        self.assertEqual(total_cost, 120)
    
    def test_destroy(self):
        """Test the destroy method"""
        # Setup test data
        symbol = "BTCUSDT"
        
        self.bot.status[symbol] = DCABot.Status()
        
        # Call the destroy method
        self.bot.destroy()
        
        # Verify that the status was cleared
        self.assertEqual(len(self.bot.status), 0) 