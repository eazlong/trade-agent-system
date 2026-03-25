from django.db import models
from utils.base_model import BaseModel

class TbUserNotifyConfig(BaseModel):
    params = models.CharField(max_length=255, blank=True, null=True)
    repeat = models.IntegerField(blank=True, null=True)
    expire = models.DateTimeField(blank=True, null=True)
    user_id = models.IntegerField(blank=True, null=True)
    template_id = models.IntegerField(blank=True, null=True)
    websocket_url = models.TextField(blank=True, null=True)
    websocket_keyword = models.CharField(max_length=16, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'tb_user_notify_config'
