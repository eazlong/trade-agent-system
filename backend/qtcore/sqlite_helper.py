import logging
from assistant.models.kline import Kline
from utils.singleton import singleton

@singleton
class SQLiteHelper:
    """
    Helper class to interact with Django ORM for K-line data.
    """

    def __init__(self):
        try:
            logging.info("SQLiteHelper (ORM) initialized successfully.")
        except Exception as e:
            logging.exception(e)
            logging.error(f"Failed to initialize SQLite ORM helper: {e}")

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
        Query K-line data via Django ORM.
        """
        try:
            qs = Kline.objects.filter(symbol=symbol, interval=interval)
            if start_time is not None:
                qs = qs.filter(timestamp__gte=start_time)
            if end_time is not None:
                qs = qs.filter(timestamp__lte=end_time)
            qs = qs.order_by('timestamp')[:limit]

            if not qs.exists():
                return None

            data = {'timestamp': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []}
            for k in qs:
                data['timestamp'].append(k.timestamp)
                data['open'].append(k.open)
                data['high'].append(k.high)
                data['low'].append(k.low)
                data['close'].append(k.close)
                data['volume'].append(k.volume)
            return data
        except Exception as e:
            logging.error(f"Error querying ORM for klines: {e}")
            return None

    def save_kline(self, symbol, interval, data):
        """
        Save K-line data via Django ORM. Respects unique_together by ignoring conflicts.
        """
        try:
            if not data or not data.get('timestamp'):
                return

            instances = []
            for i in range(len(data['timestamp'])):
                instances.append(Kline(
                    timestamp=data['timestamp'][i],
                    symbol=symbol,
                    interval=interval,
                    open=data['open'][i],
                    high=data['high'][i],
                    low=data['low'][i],
                    close=data['close'][i],
                    volume=data['volume'][i],
                ))

            # Use ignore_conflicts to avoid IntegrityError on duplicates
            Kline.objects.bulk_create(instances, ignore_conflicts=True)
            logging.debug(f"Saved {len(instances)} klines to SQLite for {symbol} {interval} {instances[-1]}")
        except Exception as e:
            logging.exception(e)
            logging.error(f"Error saving kline data for {symbol}: {e}")

    def __del__(self):
        # No explicit DB connection management needed with Django ORM
        logging.info("SQLiteHelper (ORM) finalized.")
