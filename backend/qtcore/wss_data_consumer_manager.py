from queue import Queue

from .binance_wss import BinanceWSS
from utils.thread_pool import submit_task
from utils.singleton import singleton
import logging
from . import strategy
from datetime import datetime
from .kline_data_saver import KlineDataSaver
from .sl_tp_method import MovingStopLoss

@singleton
class WSSDataProcessorManager(object):
    config = {
        '1m': { 'time': 1, 'max_duration': 1500, 'queue': Queue() },
        '5m': { 'time': 5, 'max_duration': 1500, 'queue': Queue()  },
        '15m': { 'time': 15, 'max_duration': 1500, 'queue': Queue() },
        '1h': { 'time': 60, 'max_duration': 240, 'queue': Queue() },
        '4h': { 'time': 240, 'max_duration': 200, 'queue': Queue() },
        '1d': { 'time': 1440, 'max_duration': 200, 'queue': Queue() },
    }

    def __init__(self) -> None:
        self.consumers = {}
        self.started = False
        self.inited = False

    def get_consumers(self, name):
        return self.consumers[name]

    def _load_strategy(self):
        from strategy.models.strategy import TbStrategyTemplate
        templates = TbStrategyTemplate.objects.all()
        for template in templates:
            # cls = globals()[template.name.capitalize()+"Strategy"]
            cls = getattr(strategy, template.name+"Strategy")
            self.consumers[template.name] = cls()
            logging.info(f"create class:{self.consumers[template.name]}")

    def init(self):
        if not self.inited:
            self._load_strategy()
            self.consumers['kline'] = KlineDataSaver('kline', self.config.keys(), [])
            self.consumers['moving_stop_loss'] = MovingStopLoss()
            self.inited = True
        
    def start(self):
        if not self.started:
            submit_task(BinanceWSS(self.config).start)
            for interval, c in self.config.items():
                submit_task(self.process, interval, c['queue'])
            self.started = True

    def process(self, interval, queue):
        while True:
            try:
                info = queue.get()
                if info == None:
                    return
                
                for _, consumer in self.consumers.items():
                    if consumer.need_process(interval, info['symbol']):
                        # logging.info(f"{datetime.fromtimestamp(info['timestamp']/1000)} {info['symbol']}: {interval} {consumer.name} process")
                        submit_task(consumer.process, info['symbol'], info['data'], interval)

            except Exception as e:
                logging.error(f"WSSDataProcessorManager process error: {e}")



