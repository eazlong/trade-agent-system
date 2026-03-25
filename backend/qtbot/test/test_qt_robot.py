import unittest
from unittest.mock import MagicMock, patch
import json
from datetime import datetime

from django.test import TestCase
from django.contrib.auth import get_user_model
from qtcore.strategy import StrategySignal
from qtcore.qt_bot import QtRobot, BinanceTradeHelper, BinanceAccountHelper

User = get_user_model()

class TestQtRobot(TestCase):
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
            "test_param": "test_value"
        })
        
        # Create a concrete implementation of QtRobot for testing
        class TestRobot(QtRobot):
            def process(self, symbol, direct, config, data, interval):
                pass
                
            def _calculate_profit(self, symbol, entry_price):
                return 0.0, 0.0
        
        # Create the bot instance
        self.bot = TestRobot(self.user, "TestRobot", self.trader, self.config)
        
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
        
        # Mock the BinanceAccountHelper
        self.account_helper_patcher = patch('qtcore.qt_bot.BinanceAccountHelper')
        self.mock_account_helper = self.account_helper_patcher.start()
        self.mock_account_helper.return_value.position.return_value = 0.01
        
        # Mock the TbUserTradeRecord
        self.trade_record_patcher = patch('qtcore.qt_bot.TbUserTradeRecord')
        self.mock_trade_record = self.trade_record_patcher.start()
        self.mock_trade_record.objects.create.return_value = MagicMock()
        
        # Mock the logging
        self.logging_patcher = patch('qtcore.qt_bot.logging')
        self.mock_logging = self.logging_patcher.start()
    
    def tearDown(self):
        # Stop all patchers
        self.trade_helper_patcher.stop()
        self.account_helper_patcher.stop()
        self.trade_record_patcher.stop()
        self.logging_patcher.stop()
    
    def test_init(self):
        """Test the initialization of QtRobot"""
        self.assertEqual(self.bot.name, 'TestRobot')
        self.assertEqual(self.bot.user, self.user)
        self.assertEqual(self.bot.trade_helper, self.trader)
        self.assertEqual(self.bot.config, self.config)
        self.assertEqual(self.bot.params, json.loads(self.config.params))
        self.assertEqual(self.bot.status, {})
    
    def test_set_config(self):
        """Test the setConfig method"""
        # Create a new config
        new_config = MagicMock()
        new_config.id = 2
        new_config.symbol = ["ETHUSDT"]
        new_config.template_id = 2
        new_config.params = json.dumps({
            "new_param": "new_value"
        })
        
        # Call the setConfig method
        self.bot.setConfig(new_config)
        
        # Verify that the config was updated
        self.assertEqual(self.bot.config, new_config)
        self.assertEqual(self.bot.params, json.loads(new_config.params))
    
    def test_do(self):
        """Test the do method"""
        # Create a mock template
        template = MagicMock()
        template.id = 1
        
        # Create a mock message
        msg = MagicMock()
        
        # Create a mock data
        data = MagicMock()
        
        # Call the do method
        self.bot.do(StrategySignal(msg, template, "BTCUSDT", "LONG", data, "15m"))
        
        # Verify that process was called
        # Note: Since process is a no-op in our test implementation, we can't verify it was called
        # In a real implementation, we would need to mock the process method
    
    def test_buy(self):
        """Test the buy method"""
        # Call the buy method
        result = self.bot.buy("BTCUSDT", 100, "LONG")
        
        # Verify that binance_market_buy was called
        self.mock_trade_helper.return_value.binance_market_buy.assert_called_once_with(0.01, "LONG")
        
        # Verify that usdt2amount was called
        self.mock_trade_helper.return_value.usdt2amount.assert_called_once_with(100.0)
        
        # Verify that the result is correct
        self.assertEqual(result, {
            'price': 50000,
            'cost': 100,
            'info': {'orderId': '12345'}
        })
        
        # Verify that a trade record was created
        self.mock_trade_record.objects.create.assert_called_once()
        call_args = self.mock_trade_record.objects.create.call_args[1]
        self.assertEqual(call_args['user_id'], self.user.id)
        self.assertEqual(call_args['bot_id'], self.config.id)
        self.assertEqual(call_args['bot_name'], 'TestRobot')
        self.assertEqual(call_args['symbol'], 'BTCUSDT')
        self.assertEqual(call_args['action'], 'buy')
        self.assertEqual(call_args['side'], 'LONG')
        self.assertEqual(call_args['price'], 50000)
        self.assertEqual(call_args['order_id'], '12345')
        self.assertEqual(call_args['amount'], 0.01)
    
    def test_sell(self):
        """Test the sell method"""
        # Call the sell method
        result = self.bot.sell("BTCUSDT", 100, "LONG")
        
        # Verify that binance_market_sell was called
        self.mock_trade_helper.return_value.binance_market_sell.assert_called_once_with(0.01, "LONG")
        
        # Verify that usdt2amount was called
        self.mock_trade_helper.return_value.usdt2amount.assert_called_once_with(100.0)
        
        # Verify that the result is correct
        self.assertEqual(result, {
            'price': 50000,
            'cost': 100,
            'info': {'orderId': '12345'}
        })
        
        # Verify that a trade record was created
        self.mock_trade_record.objects.create.assert_called_once()
        call_args = self.mock_trade_record.objects.create.call_args[1]
        self.assertEqual(call_args['user_id'], self.user.id)
        self.assertEqual(call_args['bot_id'], self.config.id)
        self.assertEqual(call_args['bot_name'], 'TestRobot')
        self.assertEqual(call_args['symbol'], 'BTCUSDT')
        self.assertEqual(call_args['action'], 'sell')
        self.assertEqual(call_args['side'], 'LONG')
        self.assertEqual(call_args['price'], 50000)
        self.assertEqual(call_args['order_id'], '12345')
        self.assertEqual(call_args['amount'], 0.01)
    
    def test_close_long_position(self):
        """Test the close method for a LONG position"""
        # Call the close method
        result = self.bot.close("BTCUSDT", "LONG")
        
        # Verify that binance_market_sell was called
        self.mock_trade_helper.return_value.binance_market_sell.assert_called_once_with(0.01, "LONG")
        
        # Verify that position was called
        self.mock_account_helper.return_value.position.assert_called_once_with("BTCUSDT", "LONG")
        
        # Verify that the result is correct
        self.assertEqual(result, {
            'price': 50000,
            'cost': 100,
            'info': {'orderId': '12345'}
        })
        
        # Verify that a trade record was created
        self.mock_trade_record.objects.create.assert_called_once()
        call_args = self.mock_trade_record.objects.create.call_args[1]
        self.assertEqual(call_args['user_id'], self.user.id)
        self.assertEqual(call_args['bot_id'], self.config.id)
        self.assertEqual(call_args['bot_name'], 'TestRobot')
        self.assertEqual(call_args['symbol'], 'BTCUSDT')
        self.assertEqual(call_args['action'], 'sell')
        self.assertEqual(call_args['side'], 'LONG')
        self.assertEqual(call_args['price'], 50000)
        self.assertEqual(call_args['order_id'], '12345')
        self.assertEqual(call_args['amount'], 0.01)
    
    def test_close_short_position(self):
        """Test the close method for a SHORT position"""
        # Call the close method
        result = self.bot.close("BTCUSDT", "SHORT")
        
        # Verify that binance_market_buy was called
        self.mock_trade_helper.return_value.binance_market_buy.assert_called_once_with(0.01, "SHORT")
        
        # Verify that position was called
        self.mock_account_helper.return_value.position.assert_called_once_with("BTCUSDT", "SHORT")
        
        # Verify that the result is correct
        self.assertEqual(result, {
            'price': 50000,
            'cost': 100,
            'info': {'orderId': '12345'}
        })
        
        # Verify that a trade record was created
        self.mock_trade_record.objects.create.assert_called_once()
        call_args = self.mock_trade_record.objects.create.call_args[1]
        self.assertEqual(call_args['user_id'], self.user.id)
        self.assertEqual(call_args['bot_id'], self.config.id)
        self.assertEqual(call_args['bot_name'], 'TestRobot')
        self.assertEqual(call_args['symbol'], 'BTCUSDT')
        self.assertEqual(call_args['action'], 'buy')
        self.assertEqual(call_args['side'], 'SHORT')
        self.assertEqual(call_args['price'], 50000)
        self.assertEqual(call_args['order_id'], '12345')
        self.assertEqual(call_args['amount'], 0.01)
    
    def test_close_no_position(self):
        """Test the close method when no position exists"""
        # Mock the position method to return 0
        self.mock_account_helper.return_value.position.return_value = 0
        
        # Call the close method
        result = self.bot.close("BTCUSDT", "LONG")
        
        # Verify that binance_market_sell was not called
        self.mock_trade_helper.return_value.binance_market_sell.assert_not_called()
        
        # Verify that position was called
        self.mock_account_helper.return_value.position.assert_called_once_with("BTCUSDT", "LONG")
        
        # Verify that the result is None
        self.assertIsNone(result)
        
        # Verify that a trade record was not created
        self.mock_trade_record.objects.create.assert_not_called()
    
    def test_close_all(self):
        """Test the close_all method"""
        # Call the close_all method
        self.bot.close_all("BTCUSDT")
        
        # Verify that close_all_positions was called
        self.mock_trade_helper.return_value.close_all_positions.assert_called_once_with(symbol="BTCUSDT")
    
    def test_destroy(self):
        """Test the destroy method"""
        # Call the destroy method
        self.bot.destroy()
        
        # Verify that the status was cleared
        self.assertEqual(self.bot.status, {})
        self.assertIsNone(self.bot.params)
        self.assertIsNone(self.bot.trade_helper) 