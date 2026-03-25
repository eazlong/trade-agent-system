from django.apps import AppConfig
from core import settings
from utils.thread_pool import thread_pool


import logging
import sys

class NotifyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notify'
    
    def ready(self):
        import atexit
        atexit.register(self.shutdown_thread_pool)
        if not settings.TESTING and not 'migrat' in sys.argv[1]:
            self.setup_initial_data()
    
    def shutdown_thread_pool(self):
        thread_pool.shutdown(wait=True)

    def setup_initial_data(self):
        from .models.notify import TbUserNotifyConfig
        from strategy.models import TbStrategyTemplate
        from qtcore.wss_data_consumer_manager import WSSDataProcessorManager
        from qtcore.notifier import Notify
        
        WSSDataProcessorManager().init()
        configs = TbUserNotifyConfig.objects.all()
        for config in configs:
            try:
                obj = TbStrategyTemplate.objects.get(id=config.template_id)
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.get(id=config.user_id)
                WSSDataProcessorManager().get_consumers(obj.name).register(config.user_id, Notify(user))
            except Exception as e:
                logging.exception(e)

        WSSDataProcessorManager().start()
        logging.info("notify app ready")

