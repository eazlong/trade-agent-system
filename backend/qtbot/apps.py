from django.apps import AppConfig
from core import settings
# from utils.thread_pool import thread_pool
import logging
import sys

class QtRebotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'qtbot'
    
    def ready(self):
        # import atexit
        # atexit.register(self.shutdown_thread_pool)
        if not settings.TESTING and not 'migrat' in sys.argv[1]:
            self.setup_initial_data()

            from qtcore.scheduler import start
            # logger = logging.getLogger("apscheduler")
            # logger.setLevel("WARN")
            start()
        pass

    # def shutdown_thread_pool(self):
    #     thread_pool.shutdown(wait=True)

    def setup_initial_data(self):
        from qtcore import qt_bot
        from .models.config import TbUserBotConfig
        from qtcore.trade_helper_manager import TradeHelperManager
        from strategy.models.strategy import TbStrategyTemplate
        from qtcore.wss_data_consumer_manager import WSSDataProcessorManager

        configs = TbUserBotConfig.objects.filter(deleted=0)
        for config in configs:
            try:
                obj = TbStrategyTemplate.objects.get(id=config.template_id)
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.get(id=config.user_id)
                trader = TradeHelperManager().helper(user.id)
                Bot = getattr(qt_bot, config.bot_name+"Bot")
                WSSDataProcessorManager().get_consumers(obj.name).register(config.user_id, Bot(user, trader, config))
            except Exception as e:
                logging.exception(e)

        from qtcore.private_data_consumer_manager import PrivateDataConsumerManager
        PrivateDataConsumerManager().start()
