from datetime import datetime
from utils.redis_cache import RedisDict
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .strategy import StrategySignalProcessor
import requests
import json
import logging
import time

def send_message_to_websocket(username, msg, t=None):
    channel_layer = get_channel_layer()
    name = f'{username}_group'
    async_to_sync(channel_layer.group_send)(name,
    {
        'type': 'send_message',
        'message': f'{msg}',
        'time': t if t else time.time()
    })

def notify_dingtalk(url, keywords, msg):
    try:
        data = {
                "msgtype":"text",
                "text":{
                    "content":  msg + " " + keywords
            }
        }
        r = requests.post(url, headers={"content-type":"application/json"}, data=json.dumps(data))
    except Exception as e:
        logging.exception(e)

class Notify(StrategySignalProcessor):
    class History:
        def __init__(self, msg, user_id, time):
            self.time = time
            self.msg = msg
            self.user_id = user_id

    def __init__(self, user) -> None:
        super().__init__('notify')
        self.user = user
        self.configs = {}
        self._load_configs()
        self._history = RedisDict(f'NotifyHistory_{self.user.id}', {})
        
    @property
    def history(self):
        return self._history
            
    def _load_configs(self):
        from notify.models.notify import TbUserNotifyConfig
        configs = TbUserNotifyConfig.objects.filter(user_id=self.user.id).all()
        for config in configs:
            self.configs[config.template_id] = config

    def do(self, signal):
        key = f"{signal.symbol}_{signal.template.id}_{signal.direct}_{signal.interval}"
        if signal.template.id in self.configs:
            config = self.configs[signal.template.id]
            if config.params == 'ALL' or config.params.find(signal.symbol) != -1:
                try:
                    # Check if the same message was sent in the last 5 minutes
                    import time
                    current_time = time.time()
                    if key in self._history:
                        last_sent_time = self._history[key].time
                        if current_time - last_sent_time < 300:
                            return
                        
                    # import pytz
                    # tz = pytz.timezone('Asia/Shanghai') 
                    # time = datetime.now(tz).strftime("%H:%M:%S")
                    # msg = time + " " + signal.msg
                    
                    # Update history and maintain max size
                    msg = signal.msg
                    self._history[key] = Notify.History(msg, self.user.id, current_time)
                    if len(self._history) > 100:
                        oldest_msg = min(self._history.items(), key=lambda x: x[1].time)[0]
                        del self._history[oldest_msg]
                    
                    # Send notifications
                    send_message_to_websocket(self.user.username, msg)
                    if config.websocket_url != None and config.websocket_url != '':
                        notify_dingtalk(config.websocket_url, config.websocket_keyword, msg)
                except Exception as e:
                    logging.exception(e)

    def destroy(self):
        pass

# class DingtalkNotify(Notify):
#     def notify(self, url, keywords, msg):
#         notify_dingtalk(url, keywords, msg)

# class WssNotify(Notify):
#     def notify(self, url, keywords, msg):
#         notify_dingtalk(url, keywords, msg)
