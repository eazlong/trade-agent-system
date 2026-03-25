from queue import Queue

from trade.models.config import TbUserTradingConfig

from .binance_private_wss import BinancePrivateWebsocketClient
from utils.thread_pool import submit_task
from utils.singleton import singleton
import logging

@singleton
class PrivateDataConsumerManager(object):
    def __init__(self) -> None:
        self.consumers = {}
        self.started = False
        self.inited = False

    def register(self, user_id, consumer):
        self.consumers[user_id] = {}
        self.consumers[user_id][consumer.name] = consumer

    def get_consumer(self, user_id, name):
        return self.consumers[user_id][name]
        
    def start(self):
        if not self.started:
            from utils.user_utils import get_all_user
            users = get_all_user()
            for user in users:
                if TbUserTradingConfig.objects.filter(user_id=user.id).exists():
                    config = TbUserTradingConfig.objects.get(user_id=user.id)
                    if config.is_sandbox:
                        continue
                    q = Queue()
                    if config.exchange_type == "binance":
                        submit_task(BinancePrivateWebsocketClient(user.id, q).start)
                    elif config.exchange_type == "okx":
                        pass
                        # submit_task(OkxPrivateWebsocketClient(user.id, q).start)
                    submit_task(self.process, user.id, q)
                self.started = True

    def process(self, user_id, queue):
        while True:
            try:
                info = queue.get()
                if info == None:
                    return
                
                if user_id not in self.consumers:
                    return
                
                for _, consumer in self.consumers[user_id].items():
                    submit_task(consumer.private_data_process, info)

            except Exception as e:
                logging.error(f"PrivateDataConsumerManager process error: {str(e)}")
                logging.exception(e)



