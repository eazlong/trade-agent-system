import logging
from datetime import datetime
import threading
import time
from collections import defaultdict

# from .influxdb_helper import InfluxDBHelper
from .sqlite_helper import SQLiteHelper
from .i_wss_data_consumer import WSSDataConsumer


class KlineDataSaver(WSSDataConsumer):
    """
    Saves K-line data from a WebSocket stream to databases with batch writing and deduplication.
    """
    
    def __init__(self, name, interval, symbols):
        super().__init__(name, interval, symbols)
        self.buffer = defaultdict(list)  # Store data in memory buffer
        self.buffer_lock = threading.Lock()  # Thread safety for buffer access
        self.last_write_time = time.time()  # Track last write time
        self.write_interval = 60  # Write to DB every 60 seconds
        
        # Start background thread for periodic writes
        self.writer_thread = threading.Thread(target=self._periodic_writer, daemon=True)
        self.writer_thread.start()
        
        logging.info(f"KlineDataSaver for {self.name} initialized with batch interval {self.write_interval}s.")

    def need_process(self, interval, symbol):
        """
        Check if the data needs to be processed by this consumer.
        """
        # return interval in self.interval
        return interval == '4h' or interval == '1d' or interval == '1h'

    def process(self, symbol, data, interval):
        """
        Process the K-line data and store it in memory buffer.
        Data will be written to database periodically in batches.
        """

        try:
            # Add data to buffer with thread safety
            with self.buffer_lock:
                # Create a copy of the data to avoid reference issues
                data_copy = {
                    'timestamp': list(data['timestamp']),
                    'open': list(data['open']),
                    'high': list(data['high']),
                    'low': list(data['low']),
                    'close': list(data['close']),
                    'volume': list(data['volume'])
                }
                self.buffer[(symbol, interval)].append(data_copy)
                
            logging.debug(f"Buffered kline data for {symbol} {interval}. Buffer size: {len(self.buffer[(symbol, interval)])}")
            
        except Exception as e:
            logging.exception(e)
            logging.error(f"Error buffering kline data for {symbol}: {e}")

    def _periodic_writer(self):
        """
        Background thread function that writes buffered data to database periodically.
        """
        while True:
            try:
                time.sleep(10)  # Check every 10 seconds
                
                # Check if it's time to write
                current_time = time.time()
                if current_time - self.last_write_time >= self.write_interval:
                    self._write_buffered_data()
                    self.last_write_time = current_time
                    
            except Exception as e:
                logging.error(f"Error in periodic writer thread: {e}")

    def _write_buffered_data(self):
        """
        Write all buffered data to databases.
        """
        try:
            if not self.buffer:
                return
                
            with self.buffer_lock:
                # Copy buffer data and clear buffer
                buffer_copy = dict(self.buffer)
                self.buffer.clear()
            
            # Process each symbol-interval combination
            for (symbol, interval), data_batches in buffer_copy.items():
                if not data_batches:
                    continue
                    
                # Merge all batches for this symbol-interval
                merged_data = self._merge_data_batches(data_batches)
                
                if not merged_data or not merged_data['timestamp']:
                    continue
                
                # Deduplicate data
                deduplicated_data = self._deduplicate_data(symbol, interval, merged_data)
                
                if not deduplicated_data or not deduplicated_data['timestamp']:
                    continue
                
                # Write to both databases
                try:
                    # Write to SQLite
                    SQLiteHelper().save_kline(symbol, interval, deduplicated_data)
                    logging.debug(f"Saved {len(deduplicated_data['timestamp'])} klines to SQLite for {symbol} {interval}")
                except Exception as e:
                    logging.error(f"Error saving kline data to SQLite for {symbol} {interval}: {e}")
                
                # try:
                #     # Write to InfluxDB
                #     influx_helper = InfluxDBHelper()
                #     if influx_helper.client:  # Check if InfluxDB client is available
                #         influx_helper.save_kline(symbol, interval, deduplicated_data)
                #         logging.debug(f"Saved {len(deduplicated_data['timestamp'])} klines to InfluxDB for {symbol} {interval}")
                #     else:
                #         logging.warning(f"InfluxDB client not available, skipping write for {symbol} {interval}")
                # except Exception as e:
                #     logging.error(f"Error saving kline data to InfluxDB for {symbol} {interval}: {e}")
                    
        except Exception as e:
            logging.exception(e)
            logging.error(f"Error writing buffered data: {e}")

    def _merge_data_batches(self, data_batches):
        """
        Merge multiple data batches into a single data structure.
        """
        if not data_batches:
            return {}
            
        merged = {
            'timestamp': [],
            'open': [],
            'high': [],
            'low': [],
            'close': [],
            'volume': []
        }
        
        for batch in data_batches:
            for key in merged.keys():
                if key in batch:
                    merged[key].extend(batch[key])
                    
        return merged

    def _deduplicate_data(self, symbol, interval, data):
        """
        Remove duplicate entries based on timestamp.
        """
        if not data or not data['timestamp']:
            return data
            
        # Create a dictionary to hold unique entries by timestamp
        unique_data = {}
        for i in range(len(data['timestamp'])):
            timestamp = data['timestamp'][i]
            unique_data[timestamp] = {
                'open': data['open'][i],
                'high': data['high'][i],
                'low': data['low'][i],
                'close': data['close'][i],
                'volume': data['volume'][i]
            }
        
        # Convert back to lists
        result = {
            'timestamp': list(unique_data.keys()),
            'open': [unique_data[ts]['open'] for ts in unique_data.keys()],
            'high': [unique_data[ts]['high'] for ts in unique_data.keys()],
            'low': [unique_data[ts]['low'] for ts in unique_data.keys()],
            'close': [unique_data[ts]['close'] for ts in unique_data.keys()],
            'volume': [unique_data[ts]['volume'] for ts in unique_data.keys()]
        }
        
        # Sort by timestamp
        sorted_indices = sorted(range(len(result['timestamp'])), key=lambda i: result['timestamp'][i])
        for key in result.keys():
            result[key] = [result[key][i] for i in sorted_indices]
            
        return result

    def __del__(self):
        """
        Ensure any remaining data is written before object destruction.
        """
        try:
            self._write_buffered_data()
            logging.info("KlineDataSaver flushed remaining data on destruction.")
        except Exception as e:
            logging.error(f"Error flushing data on destruction: {e}")
        pass