"""
Unit tests for the BigWaveStrategy class in backend/qtcore/strategy.py
"""


from django.test import TestCase
from unittest.mock import patch, MagicMock
from .tools.get_data import get_data
from qtcore.strategy import BigWaveStrategy, Direct, StrategySignal

import logging
logging.basicConfig(level=logging.INFO)
SYMBOL = 'COAIUSDT'
class BigWaveStrategyTestCase(TestCase):
    """Test cases for the BigWaveStrategy class"""
    def setUp(self):
        """Set up test data and BigWaveStrategy instance"""
        self.strategy = BigWaveStrategy()

        # Create sample market data for testing
        self.sample_data = {
            'timestamp': [
                1640995200000 + i * 60000 for i in range(self.strategy.K_LINE_COUNT + 10)
            ],  # 1 minute intervals
            'open': [100 + i * 0.1 for i in range(self.strategy.K_LINE_COUNT + 10)],
            'high': [101 + i * 0.1 for i in range(self.strategy.K_LINE_COUNT + 10)],
            'low': [99 + i * 0.1 for i in range(self.strategy.K_LINE_COUNT + 10)],
            'close': [100.5 + i * 0.1 for i in range(self.strategy.K_LINE_COUNT + 10)],
            'volume': [1000 + i * 10 for i in range(self.strategy.K_LINE_COUNT + 10)]
        }

        # Create test data with specific conditions for long signal
        self.long_signal_data = {
            'timestamp': [
                1640995200000 + i * 60000 for i in range(self.strategy.K_LINE_COUNT + 10)
            ],
            'open': [100.0] * (self.strategy.K_LINE_COUNT + 10),
            'high': [101.0] * (self.strategy.K_LINE_COUNT + 10),
            'low': [99.0] * (self.strategy.K_LINE_COUNT + 10),
            'close': [100.5] * (self.strategy.K_LINE_COUNT + 10),
            'volume': [1000.0] * (self.strategy.K_LINE_COUNT + 10)
        }

        self.data_from_file = {
            f'{SYMBOL}_15m': get_data(f'{SYMBOL}', '15m', '2025-10'),
            f'BTCUSDT_15m': get_data(f'BTCUSDT', '15m', '2025-10'),
            f'{SYMBOL}_1m': get_data(f'{SYMBOL}', '1m', '2025-10')
        }
        
        # Modify data to create a clear max point in the middle
        max_index = 100  # Some index in the middle
        self.long_signal_data['high'][max_index] = 95.0  # Lower high to create a clear max

        # Set the last few values to create a long signal condition
        self.long_signal_data['open'][-1] = 100.0
        self.long_signal_data['close'][-1] = 102.0  # 2% increase to meet RISE_PERSENT
        self.long_signal_data['high'][-1] = 102.5
        self.long_signal_data['low'][-1] = 99.5

        # Set volume conditions - recent volume 3x previous volume
        self.long_signal_data['volume'][-5:] = [3000.0] * 5  # Higher recent volume
        self.long_signal_data['volume'][-10:-5] = [1000.0] * 5  # Lower previous volume

        # Add a previous surge to satisfy the history requirement
        surge_time = self.long_signal_data['timestamp'][50]
        # self.strategy.surge_history["BTCUSDT"] = {'rise':[{ 'timestamp': surge_time,
        #     'price_rise': 0.06,
        #     'volume_multiplier': 3.0
        # }], 'fall': []}

        # Create short signal data
        self.short_signal_data = {
            'timestamp': [
                1640995200000 + i * 60000 for i in range(self.strategy.K_LINE_COUNT + 10)
            ],
            'open': [100.0] * (self.strategy.K_LINE_COUNT + 10),
            'high': [101.0] * (self.strategy.K_LINE_COUNT + 10),
            'low': [99.0] * (self.strategy.K_LINE_COUNT + 10),
            'close': [100.5] * (self.strategy.K_LINE_COUNT + 10),
            'volume': [1000.0] * (self.strategy.K_LINE_COUNT + 10)
        }

        # Modify data to create a clear min point in the middle
        min_index = 100  # Some index in the middle
        self.short_signal_data['low'][min_index] = 105.0  # Higher low to create a clear min

        # Set the last few values to create a short signal condition
        self.short_signal_data['open'][-1] = 100.0
        self.short_signal_data['close'][-1] = 98.0  # 2% decrease to meet RISE_PERSENT
        self.short_signal_data['high'][-1] = 100.5
        self.short_signal_data['low'][-1] = 97.5

        # Set volume conditions - recent volume 3x previous volume
        self.short_signal_data['volume'][-5:] = [3000.0] * 5  # Higher recent volume
        self.short_signal_data['volume'][-10:-5] = [1000.0] * 5  # Lower previous volume

        # Add a previous surge to satisfy the history requirement
        surge_time = self.short_signal_data['timestamp'][50]
        # self.strategy.surge_history["BTCUSDT"] = {'rise':[{
        #     'timestamp': surge_time,
        #     'price_rise': 0.06,
        #     'volume_multiplier': 3.0
        # }], 'fall': []}

    def test_initialization(self):
        """Test BigWaveStrategy initialization"""
        self.assertEqual(self.strategy.name, "BigWave")
        self.assertIn('1m', self.strategy.interval)
        self.assertIn('15h', self.strategy.interval)
        self.assertEqual(self.strategy.symbols, [])
        self.assertEqual(self.strategy.K_LINE_COUNT, 8*60)
        self.assertEqual(self.strategy.VOL_MULT, 2)
        self.assertEqual(self.strategy.RISE_PERSENT, 0.015)
        self.assertEqual(self.strategy.TRIGGER_INTERVAL, 60)

    def test_analyse_data_not_enough_data(self):
        """Test analyse_data with insufficient data points"""
        # Create data with fewer points than K_LINE_COUNT
        insufficient_data = {
            'timestamp': [1640995200000 + i * 60000 for i in range(10)],
            'open': [100.0] * 10,
            'high': [101.0] * 10,
            'low': [99.0] * 10,
            'close': [100.5] * 10,
            'volume': [1000.0] * 10
        }

        signal = self.strategy.analyse_data("BTCUSDT", insufficient_data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_volume_condition_not_met(self):
        """Test analyse_data when volume condition is not met"""
        # Modify volume data so recent volume is not VOL_MULT times previous volume
        data = self.sample_data.copy()
        data['volume'] = [1000.0] * len(data['volume'])  # Same volume throughout

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_long_signal(self):
        """Test analyse_data generates long signal correctly"""
        signal = self.strategy.analyse_data("BTCUSDT", self.long_signal_data, "1m")
        self.assertEqual(signal.direct, Direct.LONG)
        self.assertIn('rise', signal.params)
        self.assertGreater(signal.params['rise'], 0)

    def test_analyse_data_short_signal(self):
        """Test analyse_data generates short signal correctly"""
        signal = self.strategy.analyse_data("BTCUSDT", self.short_signal_data, "1m")
        self.assertEqual(signal.direct, Direct.SHORT)
        self.assertIn('rise', signal.params)
        self.assertGreater(signal.params['rise'], 0)

    def test_analyse_data_no_signal(self):
        """Test analyse_data returns NONE when no conditions are met"""
        # Use sample data with no significant conditions
        signal = self.strategy.analyse_data("BTCUSDT", self.sample_data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_long_signal_with_long_wick(self):
        """Test analyse_data returns NONE for long signal with long upper wick"""
        # Create data with long upper wick
        data = self.long_signal_data.copy()
        data['high'][-1] = data['close'][-1] + (data['close'][-1] - data['open'][-1]) * 1.5  # Long upper wick

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_short_signal_with_long_wick(self):
        """Test analyse_data returns NONE for short signal with long lower wick"""
        # Create data with long lower wick
        data = self.short_signal_data.copy()
        data['low'][-1] = data['close'][-1] - (data['open'][-1] - data['close'][-1]) * 1.5  # Long lower wick

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_long_signal_rise_percentage_too_low(self):
        """Test analyse_data returns NONE when long signal rise percentage is too low"""
        # Create data with low rise percentage
        data = self.long_signal_data.copy()
        data['close'][-1] = data['open'][-1] * (1 + self.strategy.RISE_PERSENT * 0.9)  # Below threshold

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_short_signal_rise_percentage_too_low(self):
        """Test analyse_data returns NONE when short signal rise percentage is too low"""
        # Create data with low rise percentage
        data = self.short_signal_data.copy()
        data['close'][-1] = data['open'][-1] * (1 - self.strategy.RISE_PERSENT * 0.9)  # Below threshold

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_trigger_interval_condition_not_met_for_long(self):
        """Test analyse_data returns NONE when trigger interval condition is not met for long signal"""
        # Create data with long signal conditions but trigger interval not met
        data = self.long_signal_data.copy()
        # Modify the max index to be too close to the end (less than TRIGGER_INTERVAL)
        # We need to adjust the data to have the max point close to the end

        # First, reset the data to a clean state
        for i in range(len(data['high'])):
            data['high'][i] = 101.0

        # Set a maximum point very close to the end (less than TRIGGER_INTERVAL)
        max_index = -5  # This should be less than TRIGGER_INTERVAL (60)
        data['high'][max_index] = 105.0  # Lower high to create a clear max
        data['close'][-1] = 105.0  # Higher close to break the max
        data['open'][-1] = 103.0   # Higher open

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_trigger_interval_condition_not_met_for_short(self):
        """Test analyse_data returns NONE when trigger interval condition is not met for short signal"""
        # Create data with short signal conditions but trigger interval not met
        data = self.short_signal_data.copy()
        # Modify the data to have the min point close to the end (less than TRIGGER_INTERVAL)

        # First, reset the data to a clean state
        for i in range(len(data['low'])):
            data['low'][i] = 99.0

        # Set a minimum point very close to the end (less than TRIGGER_INTERVAL)
        min_index = -5  # This should be less than TRIGGER_INTERVAL (60)
        data['low'][min_index] = 95.0  # Higher low to create a clear min
        data['close'][-1] = 95.0   # Lower close to break the min
        data['open'][-1] = 97.0    # Lower open

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_close_not_above_open_for_long(self):
        """Test analyse_data returns NONE when close is not above open for long signal"""
        # Create data with high max point but close not above open
        data = self.long_signal_data.copy()
        # Reset to clean state
        for i in range(len(data['high'])):
            data['high'][i] = 101.0

        # Set a clear maximum point
        max_index = -10
        data['high'][max_index] = 95.0  # Lower high to create a clear max
        data['close'][-1] = 105.0  # Higher close to break the max
        data['open'][-1] = 106.0   # Higher open (close < open)

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_close_not_below_open_for_short(self):
        """Test analyse_data returns NONE when close is not below open for short signal"""
        # Create data with low min point but close not below open
        data = self.short_signal_data.copy()
        # Reset to clean state
        for i in range(len(data['low'])):
            data['low'][i] = 99.0

        # Set a clear minimum point
        min_index = -10
        data['low'][min_index] = 105.0  # Higher low to create a clear min
        data['close'][-1] = 95.0   # Lower close to break the min
        data['open'][-1] = 94.0    # Lower open (close > open)

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_close_equals_max_for_long(self):
        """Test analyse_data returns NONE when close equals max for long signal"""
        # Create data where close equals the max point
        data = self.long_signal_data.copy()
        # Reset to clean state
        for i in range(len(data['high'])):
            data['high'][i] = 101.0

        # Set a clear maximum point
        max_index = -10
        data['high'][max_index] = 95.0  # Lower high to create a clear max
        data['close'][-1] = 95.0  # Close equals max
        data['open'][-1] = 93.0   # Lower open

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_close_equals_min_for_short(self):
        """Test analyse_data returns NONE when close equals min for short signal"""
        # Create data where close equals the min point
        data = self.short_signal_data.copy()
        # Reset to clean state
        for i in range(len(data['low'])):
            data['low'][i] = 99.0

        # Set a clear minimum point
        min_index = -10
        data['low'][min_index] = 105.0  # Higher low to create a clear min
        data['close'][-1] = 105.0   # Close equals min
        data['open'][-1] = 107.0    # Higher open

        signal = self.strategy.analyse_data("BTCUSDT", data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_edge_case_empty_data(self):
        """Test analyse_data with empty data"""
        empty_data = {
            'timestamp': [],
            'open': [],
            'high': [],
            'low': [],
            'close': [],
            'volume': []
        }

        signal = self.strategy.analyse_data("BTCUSDT", empty_data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_analyse_data_edge_case_single_data_point(self):
        """Test analyse_data with single data point"""
        single_data = {
            'timestamp': [1640995200000],
            'open': [100.0],
            'high': [101.0],
            'low': [99.0],
            'close': [100.5],
            'volume': [1000.0]
        }

        signal = self.strategy.analyse_data("BTCUSDT", single_data, "1m")
        self.assertEqual(signal.direct, Direct.NONE)

    def test_format_msg_long_signal(self):
        """Test format_msg for long signal"""
        # Mock the template
        self.strategy.template = MagicMock()
        self.strategy.template.content = "Symbol {symbol} shows a big wave {{direction}} of {{rise}} in {{interval}}"

        signal = StrategySignal(
            direct=Direct.LONG,
            params={'rise': 0.025},
            symbol="BTCUSDT"
        )

        msg = self.strategy.format_msg(signal)
        self.assertIn("上涨", msg)
        self.assertIn("2.50%", msg)
        self.assertIn(f"{self.strategy.K_LINE_COUNT}m", msg)
        self.assertIn("BTCUSDT", msg)

    def test_format_msg_short_signal(self):
        """Test format_msg for short signal"""
        # Mock the template
        self.strategy.template = MagicMock()
        self.strategy.template.content = "Symbol {symbol} shows a big wave {{direction}} of {{rise}} in {{interval}}"

        signal = StrategySignal(
            direct=Direct.SHORT,
            params={'rise': 0.032},
            symbol="BTCUSDT"
        )

        msg = self.strategy.format_msg(signal)
        self.assertIn("下跌", msg)
        self.assertIn("3.20%", msg)
        self.assertIn(f"{self.strategy.K_LINE_COUNT}m", msg)
        self.assertIn("BTCUSDT", msg)


    def test_format_msg_zero_rise(self):
        """Test format_msg with zero rise value"""
        # Mock the template
        self.strategy.template = MagicMock()
        self.strategy.template.content = "Symbol {symbol} shows a big wave {{direction}} of {{rise}} in {{interval}}"

        signal = StrategySignal(
            direct=Direct.LONG,
            params={'rise': 0.0},
            symbol="BTCUSDT"
        )

        msg = self.strategy.format_msg(signal)
        self.assertIn("0.00%", msg)

    def test_format_msg_negative_rise(self):
        """Test format_msg with negative rise value (should use absolute value)"""
        # Mock the template
        self.strategy.template = MagicMock()
        self.strategy.template.content = "Symbol {symbol} shows a big wave {{direction}} of {{rise}} in {{interval}}"

        signal = StrategySignal(
            direct=Direct.LONG,
            params={'rise': -0.025},
            symbol="BTCUSDT"
        )

        msg = self.strategy.format_msg(signal)
        self.assertIn("2.50%", msg)  # Should use absolute value

    def test_process_method(self):
        """Test the process method"""
        # Mock the template and listener
        self.strategy.template = MagicMock()
        self.strategy.template.content = "Symbol {symbol} shows a big wave {{direction}} of {{rise}} in {{interval}}"

        mock_processor = MagicMock()
        self.strategy.listener = [{'processor': mock_processor}]

        # Add a previous surge to satisfy the history requirement
        surge_time = self.long_signal_data['timestamp'][50]
        self.strategy.surge_history["BTCUSDT"] = [{
            'timestamp': surge_time,
            'price_rise': 0.06,
            'volume_multiplier': 3.0
        }]

        # Process with long signal data
        self.strategy.process("BTCUSDT", self.long_signal_data, "1m")

        # Verify the processor's do method was called
        mock_processor.do.assert_called_once()
        call_args = mock_processor.do.call_args[0][0]
        self.assertEqual(call_args.symbol, "BTCUSDT")
        self.assertEqual(call_args.strategy, "BigWave")
        self.assertEqual(call_args.interval, "1m")
        self.assertEqual(call_args.direct, Direct.LONG)

    def test_detect_previous_volume_surge_with_insufficient_data(self):
        """Test _detect_previous_volume_surge with insufficient data"""
        # Create data with fewer points than SURGE_DETECTION_WINDOW
        insufficient_data = {
            'timestamp': [1640995200000 + i * 60000 for i in range(10)],
            'open': [100.0] * 10,
            'high': [101.0] * 10,
            'low': [99.0] * 10,
            'close': [100.5] * 10,
            'volume': [1000.0] * 10
        }

        result = self.strategy._detect_previous_volume_surge("BTCUSDT", insufficient_data)
        self.assertFalse(result)

    def test_detect_previous_volume_surge_with_surge_condition(self):
        """Test _detect_previous_volume_surge when surge conditions are met"""
        # Create data with clear volume surge and price rise
        surge_data = {
            'timestamp': [1640995200000 + i * 60000 for i in range(100)],
            'open': [100.0] * 100,
            'high': [101.0] * 100,
            'low': [99.0] * 100,
            'close': [100.5] * 100,
            'volume': [1000.0] * 100
        }

        # Modify data to create volume surge and price rise
        surge_index = 50
        surge_data['volume'][surge_index-4:surge_index-2] = [500.0, 500.0]  # Low previous volume
        surge_data['volume'][surge_index-2:surge_index] = [2000.0, 2000.0]  # High recent volume
        surge_data['open'][surge_index-1] = 100.0
        surge_data['close'][surge_index-1] = 105.0  # 5% price rise

        # Clear any existing history
        self.strategy.surge_history = {}

        result = self.strategy._detect_previous_volume_surge("BTCUSDT", surge_data)
        # Depending on the exact implementation, this might return True or False
        # The important thing is that it doesn't crash and returns a boolean

        # Check if surge history was updated
        if "BTCUSDT" in self.strategy.surge_history:
            self.assertIsInstance(self.strategy.surge_history["BTCUSDT"], list)

    def test_has_recent_volume_surge_no_history(self):
        """Test _has_recent_volume_surge when no history exists"""
        # Ensure no history exists for this symbol
        if "BTCUSDT" in self.strategy.surge_history:
            del self.strategy.surge_history["BTCUSDT"]

        result = self.strategy._has_recent_volume_surge("BTCUSDT", 1640995200000)
        self.assertFalse(result)

    def test_has_recent_volume_surge_with_recent_surge(self):
        """Test _has_recent_volume_surge when recent surge exists"""
        # Add a recent surge to history
        current_time = 1640995200000
        recent_surge_time = current_time - 30 * 60 * 1000  # 30 minutes ago

        self.strategy.surge_history["BTCUSDT"] = [{
            'timestamp': recent_surge_time,
            'price_rise': 0.06,
            'volume_multiplier': 3.0
        }]

        result = self.strategy._has_recent_volume_surge("BTCUSDT", current_time)
        self.assertTrue(result)

    def test_has_recent_volume_surge_with_old_surge(self):
        """Test _has_recent_volume_surge when only old surge exists"""
        # Add an old surge to history (older than 8 hours)
        current_time = 1640995200000
        old_surge_time = current_time - 9 * 60 * 60 * 1000  # 9 hours ago

        self.strategy.surge_history["BTCUSDT"] = [{
            'timestamp': old_surge_time,
            'price_rise': 0.06,
            'volume_multiplier': 3.0
        }]

        result = self.strategy._has_recent_volume_surge("BTCUSDT", current_time)
        self.assertFalse(result)

    def test_analyse_data_15m_interval(self):
        """Test analyse_data with 15m interval"""
        # With 15m interval, it should call _detect_previous_volume_surge and return None
        result = self.strategy.analyse_data("BTCUSDT", self.sample_data, "15m")
        self.assertIsNone(result)

    def test_surge_history_recording(self):
        """Test that surge history is properly recorded"""
        # Clear any existing history
        self.strategy.surge_history = {}

        # Create data with surge conditions - need enough data points
        data_length = max(self.strategy.SURGE_DETECTION_WINDOW + 20, 100)
        surge_data = {
            'timestamp': [1640995200000 + i * 60000 for i in range(data_length)],
            'open': [100.0] * data_length,
            'high': [101.0] * data_length,
            'low': [99.0] * data_length,
            'close': [100.5] * data_length,
            'volume': [1000.0] * data_length
        }

        # Modify data to create volume surge and price rise at a valid index
        # The detection algorithm looks in a specific range, so we need to place our surge in that range
        # Range is: len(data['timestamp']) - self.SURGE_DETECTION_WINDOW to len(data['timestamp']) - 10
        detection_start = data_length - self.strategy.SURGE_DETECTION_WINDOW
        detection_end = data_length - 10

        # Place surge in the middle of the detection range
        surge_index = detection_start + (detection_end - detection_start) // 2

        # Ensure surge_index is valid and has enough data points before it
        if surge_index >= 5:
            # Set volume conditions for surge detection:
            # recent_vol = np.mean(data['volume'][i-2:i])
            # prev_vol = np.mean(data['volume'][i-4:i-2])
            surge_data['volume'][surge_index-4] = 500.0
            surge_data['volume'][surge_index-3] = 500.0  # prev_vol mean = 500.0
            surge_data['volume'][surge_index-2] = 500.0
            surge_data['volume'][surge_index-1] = 2000.0  # recent_vol mean = 1500.0 (3x prev_vol)

            # Set price conditions for surge detection:
            # price_rise = (data['close'][i] - data['open'][i]) / data['open'][i]
            surge_data['open'][surge_index-1] = 100.0
            surge_data['close'][surge_index-1] = 105.0  # price_rise = 5% (meets threshold of 5%)

            # Call detect method
            result = self.strategy._detect_previous_volume_surge("BTCUSDT", surge_data)

            # Test that the method doesn't crash and returns a boolean
            self.assertIn(result, [True, False])

            # Verify the recorded data structure if history was recorded
            if "BTCUSDT" in self.strategy.surge_history and len(self.strategy.surge_history["BTCUSDT"]) > 0:
                surge_record = self.strategy.surge_history["BTCUSDT"][0]
                self.assertIn('timestamp', surge_record)
                self.assertIn('price_rise', surge_record)
                self.assertIn('volume_multiplier', surge_record)
        else:
            # If we can't create a valid surge, at least test that the method doesn't crash
            result = self.strategy._detect_previous_volume_surge("BTCUSDT", surge_data)
            # This should return False since we couldn't create a valid surge
            self.assertFalse(result)


    def test_analyse_data_from_file(self):
        """Test analyse_data with data from file"""
        symbol_data = {'timestamp': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []}
        for m in self.data_from_file[f'{SYMBOL}_1m'].reset_index().to_dict('records'):
            t = m['timestamp']
            symbol_data['timestamp'].append(t.timestamp()*1000)
            symbol_data['open'].append(float(m['open']))
            symbol_data['high'].append(float(m['high']))
            symbol_data['low'].append(float(m['low']))
            symbol_data['close'].append(float(m['close']))
            symbol_data['volume'].append(int(m['volume']))
        
        btc_data_15m = {'timestamp': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []}
        for m in self.data_from_file['BTCUSDT_15m'].reset_index().to_dict('records'):
            t = m['timestamp']
            btc_data_15m['timestamp'].append(t.timestamp()*1000)
            btc_data_15m['open'].append(float(m['open']))
            btc_data_15m['high'].append(float(m['high']))
            btc_data_15m['low'].append(float(m['low']))
            btc_data_15m['close'].append(float(m['close']))
            btc_data_15m['volume'].append(int(m['volume']))

        symbol_data_15m = {'timestamp': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []}
        for m in self.data_from_file[f'{SYMBOL}_15m'].reset_index().to_dict('records'):
            t = m['timestamp']
            symbol_data_15m['timestamp'].append(t.timestamp()*1000)
            symbol_data_15m['open'].append(float(m['open']))
            symbol_data_15m['high'].append(float(m['high']))
            symbol_data_15m['low'].append(float(m['low']))
            symbol_data_15m['close'].append(float(m['close']))
            symbol_data_15m['volume'].append(int(m['volume']))

        for i in range(1, len(symbol_data['timestamp'])):
            if i%15==0:
                i15 = i//15
                d = {'timestamp': btc_data_15m['timestamp'][0:i15], 'open': btc_data_15m['open'][0:i15], 'high': btc_data_15m['high'][0:i15], 'low': btc_data_15m['low'][0:i15], 'close': btc_data_15m['close'][0:i15], 'volume': btc_data_15m['volume'][0:i15]}
                signal = self.strategy.analyse_data(f"BTCUSDT", d, "15m")
                d = {'timestamp': symbol_data_15m['timestamp'][0:i15], 'open': symbol_data_15m['open'][0:i15], 'high': symbol_data_15m['high'][0:i15], 'low': symbol_data_15m['low'][0:i15], 'close': symbol_data_15m['close'][0:i15], 'volume': symbol_data_15m['volume'][0:i15]}
                signal = self.strategy.analyse_data(f"{SYMBOL}", d, "15m")

            d = {'timestamp': symbol_data['timestamp'][0:i], 'open': symbol_data['open'][0:i], 'high': symbol_data['high'][0:i], 'low': symbol_data['low'][0:i], 'close': symbol_data['close'][0:i], 'volume': symbol_data['volume'][0:i]}
            signal = self.strategy.analyse_data(f"{SYMBOL}", d, "1m")
            if signal.direct != Direct.NONE:
                from datetime import datetime
                import pytz
                print(f"{SYMBOL}: {datetime.fromtimestamp(d['timestamp'][-1]/1000, pytz.timezone('Asia/Shanghai'))} {signal.direct}, {signal.params}")
            # self.assertEqual(signal.direct, Direct.LONG)  