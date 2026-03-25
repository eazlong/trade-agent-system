import unittest
from unittest.mock import MagicMock, patch
import json
from datetime import datetime

from django.test import TestCase
from django.contrib.auth import get_user_model
from qtcore.qt_bot import DCABot

User = get_user_model()

class TestGridBot(TestCase):
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
        self.config.symbol = ["BTCUSDT"]
        self.config.template_id = 1
        self.config.params = json.dumps({
            "immediate_entry": True,
            "initial_allocation_pct": 0.1,
            "min_down_pct": 0.05,
            "max_additions": 3,
            "add_pct": 0.2,
            "profit_pct": 0.03,
            "do_loss_stop": True
        })
        
        # Create the bot instance
        self.bot = DCABot(self.user, "TestGridBot", self.trader, "SHORT", self.config)
        
        # Mock the RedisDict
        self.redis_dict_patcher = patch('qtcore.qt_bot.RedisDict')
        self.mock_redis_dict = self.redis_dict_patcher.start()
        self.mock_redis_dict.return_value = {}
        
        # Mock the BinanceAccountHelper
        self.account_helper_patcher = patch('qtcore.qt_bot.BinanceAccountHelper')
        self.mock_account_helper = self.account_helper_patcher.start()
        self.mock_account_helper.return_value.balance.return_value = (0, 1000)
        self.mock_account_helper.return_value.position.return_value = 0
        
        # Mock the BinanceTradeHelper
        self.trade_helper_patcher = patch('qtcore.qt_bot.BinanceTradeHelper')
        self.mock_trade_helper = self.trade_helper_patcher.start()
        self.mock_trade_helper.return_value.usdt2amount.return_value = 0.01
        self.mock_trade_helper.return_value.binance_market_sell.return_value = {
            'price': 50000,
            'cost': 100,
            'info': {'orderId': '12345'}
        }
        self.mock_trade_helper.return_value.binance_market_buy.return_value = {
            'price': 50000,
            'cost': 100,
            'info': {'orderId': '12345'}
        }
        
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
    
    def tearDown(self):
        # Stop all patchers
        self.redis_dict_patcher.stop()
        self.account_helper_patcher.stop()
        self.trade_helper_patcher.stop()
        self.trade_record_patcher.stop()
        self.websocket_patcher.stop()
        self.logging_patcher.stop()
    
    def test_init(self):
        """Test the initialization of GridBot"""
        self.assertEqual(self.bot.name, 'TestGridBot')
        self.assertEqual(self.bot.side, "SHORT")
        self.assertEqual(self.bot.user, self.user)
        self.assertEqual(self.bot.trade_helper, self.trader)
        self.assertEqual(self.bot.config, self.config)
        self.assertEqual(self.bot.params, json.loads(self.config.params))
    
    def test_status_class(self):
        """Test the Status inner class"""
        status = DCABot.Status()
        self.assertEqual(status.entry_price, [])
        self.assertEqual(status.entry_position, [])
        self.assertEqual(status.position_count, 0)
        
        # Test string representation
        status.entry_price = [50000]
        status.entry_position = [100]
        status.position_count = 1
        self.assertEqual(str(status), "[50000] - [100] - 1")
    
    def test_entry(self):
        """Test the entry method"""
        symbol = "BTCUSDT"
        
        # Call the entry method
        self.bot.entry(symbol)
        
        # Verify that sell was called (for SHORT side)
        self.mock_trade_helper.return_value.binance_market_sell.assert_called_once()
        self.mock_trade_helper.return_value.usdt2amount.assert_called_once_with(100.0)  # 1000 * 0.1
        
        # Verify that the status was updated
        self.assertIn(symbol, self.bot.status)
        self.assertEqual(self.bot.status[symbol].position_count, 1)
        self.assertEqual(len(self.bot.status[symbol].entry_price), 1)
        self.assertEqual(len(self.bot.status[symbol].entry_position), 1)
        self.assertEqual(self.bot.status[symbol].entry_price[0], 50000)
        self.assertEqual(self.bot.status[symbol].entry_position[0], 100)
    
    def test_add(self):
        """Test the add method"""
        symbol = "BTCUSDT"
        
        # Setup existing position
        self.bot.status[symbol] = DCABot.Status()
        self.bot.status[symbol].entry_price = [50000]
        self.bot.status[symbol].entry_position = [100]
        self.bot.status[symbol].position_count = 1
        
        # Call the add method
        self.bot.add(symbol, 0.2)
        
        # Verify that sell was called (for SHORT side)
        self.mock_trade_helper.return_value.binance_market_sell.assert_called_once()
        self.mock_trade_helper.return_value.usdt2amount.assert_called_once_with(20.0)  # 100 * 0.2
        
        # Verify that the status was updated
        self.assertEqual(self.bot.status[symbol].position_count, 2)
        self.assertEqual(len(self.bot.status[symbol].entry_price), 2)
        self.assertEqual(len(self.bot.status[symbol].entry_position), 2)
        self.assertEqual(self.bot.status[symbol].entry_price[1], 50000)
        self.assertEqual(self.bot.status[symbol].entry_position[1], 100)
    
    def test_leave(self):
        """Test the leave method"""
        symbol = "BTCUSDT"
        
        # Setup existing position
        self.bot.status[symbol] = DCABot.Status()
        self.bot.status[symbol].entry_price = [50000]
        self.bot.status[symbol].entry_position = [100]
        self.bot.status[symbol].position_count = 1
        
        # Call the leave method
        self.bot.leave(symbol)
        
        # Verify that close was called
        self.mock_trade_helper.return_value.binance_market_buy.assert_called_once()
        
        # Verify that the status was updated
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
        
        # Verify that close was called for each symbol
        self.mock_trade_helper.return_value.binance_market_buy.assert_called_once()
        
        # Verify that the status was cleared
        self.assertEqual(self.bot.status, {})
        self.assertIsNone(self.bot.params)
        self.assertIsNone(self.bot.trade_helper) 