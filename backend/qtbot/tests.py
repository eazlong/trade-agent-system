from django.test import TestCase
from django.contrib.auth import get_user_model
from unittest.mock import MagicMock, patch
from qtcore.strategy import Strategy
from qtcore.qt_bot import HighWinRateShortBot
from utils.redis_cache import RedisDict
from qtbot.models import TbUserBotConfig
import json

User = get_user_model()

class HighWinRateShortBotTests(TestCase):
    def setUp(self):
        # 创建测试用户
        self.user = User.objects.create_user(
            id=1,
            username='testuser',
            password='testpass'
        )
        
        # 创建机器人配置
        self.config = TbUserBotConfig.objects.create(
            user_id=self.user.id,
            bot_name='HighWinRateShort',
            template_id=1,
            symbol='BTCUSDT',
            params=json.dumps({
                'min_down_pct': 0.04,
                'initial_allocation_pct': 0.05,
                'max_additions': 4
            }),
            usdt=1000
        )

        # 模拟交易助手
        self.mock_trader = MagicMock()
        
        # 创建机器人实例
        self.bot = HighWinRateShortBot(self.user, self.mock_trader)
        
        # 模拟价格数据
        self.mock_data = {
            'close': [30000]  # 模拟收盘价
        }

    def test_initial_short_position(self):
        """测试首次开空仓"""
        # 模拟账户余额
        self.mock_trader.balance.return_value = (0, 10000)  # (used, total)
        
        # 模拟卖出操作
        mock_sell_order = {
            'price': 30000,
            'cost': 500,  # 5% of 10000
            'amount': 0.0167  # 500/30000
        }
        self.mock_trader.binance_market_sell.return_value = mock_sell_order

        # 执行空单信号
        self.bot.process('BTCUSDT', Direct.SHORT, self.config, self.mock_data)

        # 验证是否正确开仓
        self.mock_trader.binance_market_sell.assert_called_once()
        self.assertTrue('BTCUSDT' in self.bot.status)
        self.assertEqual(self.bot.status['BTCUSDT'].position_count, 1)
        self.assertEqual(self.bot.status['BTCUSDT'].entry_price[0], 30000)

    def test_add_to_short_position(self):
        """测试加空仓"""
        # 设置初始状态
        initial_status = self.bot.Status()
        initial_status.entry_price = [30000]
        initial_status.entry_position = [500]
        initial_status.position_count = 1
        self.bot.status['BTCUSDT'] = initial_status

        # 模拟价格上涨超过4%
        self.mock_data['close'] = [31500]  # 上涨5%

        # 模拟卖出操作
        mock_sell_order = {
            'price': 31500,
            'cost': 1000,  # 双倍于之前的仓位
            'amount': 0.0317  # 1000/31500
        }
        self.mock_trader.binance_market_sell.return_value = mock_sell_order

        # 执行空单信号
        self.bot.process('BTCUSDT', Direct.SHORT, self.config, self.mock_data)

        # 验证是否正确加仓
        self.assertEqual(self.bot.status['BTCUSDT'].position_count, 2)
        self.assertEqual(len(self.bot.status['BTCUSDT'].entry_price), 2)
        self.assertEqual(self.bot.status['BTCUSDT'].entry_price[-1], 31500)

    def test_close_short_position_with_profit(self):
        """测试盈利平仓"""
        # 设置初始状态
        initial_status = self.bot.Status()
        initial_status.entry_price = [30000]
        initial_status.entry_position = [500]
        initial_status.position_count = 1
        self.bot.status['BTCUSDT'] = initial_status

        # 模拟价格下跌超过1%（产生盈利）
        self.mock_data['close'] = [29000]  # 下跌超过3%

        # 模拟平仓操作
        mock_close_order = {
            'price': 29000,
            'amount': 0.0167
        }
        self.mock_trader.position.return_value = 0.0167
        self.mock_trader.binance_market_buy.return_value = mock_close_order

        # 执行多单信号（触发平空仓）
        self.bot.process('BTCUSDT', Direct.LONG, self.config, self.mock_data)

        # 验证是否正确平仓
        self.mock_trader.binance_market_buy.assert_called_once()
        self.assertNotIn('BTCUSDT', self.bot.status)

    def test_max_additions_limit(self):
        """测试最大加仓次数限制"""
        # 设置初始状态为已经达到最大加仓次数
        initial_status = self.bot.Status()
        initial_status.entry_price = [30000, 31500, 33000, 34500]  # 4次加仓
        initial_status.entry_position = [500, 1000, 2000, 4000]
        initial_status.position_count = 4
        self.bot.status['BTCUSDT'] = initial_status

        # 模拟价格继续上涨
        self.mock_data['close'] = [36000]

        # 执行空单信号
        self.bot.process('BTCUSDT', Direct.SHORT, self.config, self.mock_data)

        # 验证没有继续加仓
        self.mock_trader.binance_market_sell.assert_not_called()
        self.assertEqual(self.bot.status['BTCUSDT'].position_count, 4)

    def test_destroy_closes_all_positions(self):
        """测试销毁机器人时平掉所有仓位"""
        # 设置初始状态
        initial_status = self.bot.Status()
        initial_status.entry_price = [30000]
        initial_status.entry_position = [500]
        initial_status.position_count = 1
        self.bot.status['BTCUSDT'] = initial_status

        # 模拟平仓操作
        mock_close_order = {
            'price': 29000,
            'amount': 0.0167
        }
        self.mock_trader.position.return_value = 0.0167
        self.mock_trader.binance_market_buy.return_value = mock_close_order

        # 销毁机器人
        self.bot.destroy()

        # 验证是否平掉所有仓位
        self.mock_trader.binance_market_buy.assert_called_once()
        self.assertEqual(len(self.bot.status), 0)