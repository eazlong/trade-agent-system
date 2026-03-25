import logging
from datetime import datetime, timedelta

from django.conf import settings
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions
from utils.singleton import singleton

@singleton
class InfluxDBHelper:
    """
    Helper class to interact with InfluxDB.
    """

    def __init__(self):
        self.client = None
        self.query_api = None
        self.write_api = None
        
        try:
            self.client = InfluxDBClient(
                url=settings.INFLUXDB_URL,
                token=settings.INFLUXDB_TOKEN,
                org=settings.INFLUXDB_ORG
            )
            self.query_api = self.client.query_api()
            self.write_api = self.client.write_api(
                write_options=WriteOptions(batch_size=100,    # 每100条数据写一次
                               flush_interval=1000, # 或者每2秒自动写入一次
                               jitter_interval=100, # 随机抖动，避免写入峰值
                               retry_interval=2000) # 失败重试间隔
            )

            logging.info("InfluxDBHelper initialized successfully.")
        except Exception as e:
            logging.exception(e)
            logging.error(f"Failed to initialize InfluxDB client: {e}")
            # Keep self.client as None to indicate unavailable state

    def _interval_to_minutes(self, interval):
        if interval.endswith('m'):
            return int(interval.replace('m', ''))
        elif interval.endswith('h'):
            return int(interval.replace('h', '')) * 60
        elif interval.endswith('d'):
            return int(interval.replace('d', '')) * 60 * 24
        else:
            raise ValueError(f"Unsupported interval: {interval}")

    def query_klines(self, symbol, interval, limit, start_time=None, end_time=None):
        """
        Query K-line data from InfluxDB.
        """
        if not self.client:
            logging.debug("InfluxDB client not available. Cannot query klines.")
            return None
        
        # if start_time:
        #     if not self._kline_exists(symbol, interval, start_time):
        #         logging.info(f"Kline not exists: {symbol} {interval} {start_time}")
        #         return None
        
        # This is a simplified way to calculate the start time.
        # A more robust solution would parse the interval string (e.g., '1m', '5m', '1h').
        # For now, assuming 'm' for minutes.
        minutes_per_interval = 0
        try:
            minutes_per_interval = self._interval_to_minutes(interval)
            start_duration = minutes_per_interval * limit
            if start_time:
                start_time = datetime.fromtimestamp(start_time / 1000)
            else:
                start_time = datetime.utcnow() - timedelta(minutes=start_duration)
        except ValueError:
            logging.error(f"Could not parse interval '{interval}'. Defaulting to 1 hour range.")
            start_time = datetime.utcnow() - timedelta(hours=1)


        query = f'''
        from(bucket: "{settings.INFLUXDB_BUCKET}")
          |> range(start: {start_time.isoformat()}Z)
          |> filter(fn: (r) => r._measurement == "kline")
          |> filter(fn: (r) => r.symbol == "{symbol}")
          |> filter(fn: (r) => r.interval == "{interval}")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["_time"], desc: false)
          |> limit(n: {limit})
        '''

        try:
            logging.debug(f"Querying InfluxDB for {symbol} with interval {interval} and limit {limit} start at {start_time.isoformat()}")
            tables = self.query_api.query(query)
            
            if not tables:
                # logging.info(f"No data found for {symbol} with interval {interval} and limit {limit}")
                return None

            data = {'timestamp': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []}
            for table in tables:
                for record in table.records:
                    t = int(record.get_time().timestamp() * 1000)
                    if len(data['timestamp']) == 0 and t > start_time.timestamp() * 1000 + minutes_per_interval * 60 * 1000:
                        break
                    if len(data['timestamp']) > 0 and t - data['timestamp'][-1] != minutes_per_interval * 60 * 1000:
                        break

                    data['timestamp'].append(t)
                    data['open'].append(record['open'])
                    data['high'].append(record['high'])
                    data['low'].append(record['low'])
                    data['close'].append(record['close'])
                    data['volume'].append(record['volume'])
            
            if len(data['timestamp']) == 0:
                # logging.info(f"No data found for {symbol} with interval {interval} and limit {limit}")
                return None

            return data
        except Exception as e:
            logging.error(f"Error querying InfluxDB: {e}")
            return None

    def _kline_exists(self, symbol, interval, ts):
        ts_start = datetime.fromtimestamp(ts / 1000)
        ts_end = datetime.fromtimestamp((ts+1) / 1000)

        query = f'''
        from(bucket: "{settings.INFLUXDB_BUCKET}")
        |> range(start: {ts_start.isoformat()}Z, stop: {ts_end.isoformat()}Z)
        |> filter(fn: (r) => r["_measurement"] == "kline")
        |> filter(fn: (r) => r["symbol"] == "{symbol}")
        |> filter(fn: (r) => r["interval"] == "{interval}")
        |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''
        # logging.info(f"Querying InfluxDB for {symbol} with interval {interval} and ts {ts}")
        result = self.query_api.query(query, org=settings.INFLUXDB_ORG)
        return len(result) > 0
    
    def last(self, symbol, interval):
        minutes_per_interval = self._interval_to_minutes(interval)
        query = f'''
        from(bucket: "{settings.INFLUXDB_BUCKET}")
        |> range(start: -10d)
        |> filter(fn: (r) => r["_measurement"] == "kline")
        |> filter(fn: (r) => r["symbol"] == "{symbol}")
        |> filter(fn: (r) => r["interval"] == "{interval}")
        |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        |> last()
        '''
        result = self.query_api.query(query, org=settings.INFLUXDB_ORG)
        return result
    
    def save_kline(self, symbol, interval, data):
        """
        Process the K-line data and save it to InfluxDB.
        """
        try:
            if not data or len(data['timestamp']) == 0:
                return
                
            points = []
            for i in range(len(data['timestamp'])):
                point = (
                    Point("kline")
                    .tag("symbol", symbol)
                    .tag("interval", interval)
                    .field("open", data['open'][i])
                    .field("high", data['high'][i])
                    .field("low", data['low'][i])
                    .field("close", data['close'][i])
                    .field("volume", data['volume'][i])
                    .time(datetime.fromtimestamp(data['timestamp'][i] / 1000))
                )
                points.append(point)
            self.write_api.write(bucket=settings.INFLUXDB_BUCKET, record=points)
            # logging.debug(f"Saved kline for {symbol} at {datetime.fromtimestamp(data['timestamp'] / 1000)}")

        except Exception as e:
            logging.exception(e)
            logging.error(f"Error processing kline data for {symbol}: {e}")

    def __del__(self):
        if hasattr(self, 'client') and self.client:
            self.client.close()
            logging.info("InfluxDB client closed.")
