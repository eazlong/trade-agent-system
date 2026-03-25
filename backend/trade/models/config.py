from enum import Flag
from django.db import models
from utils.base_model import BaseModel

class TbUserTradingConfig(BaseModel):
    user_id = models.IntegerField(blank=False, null=False)
    exchange_type = models.TextField(blank=True, null=True)
    key = models.TextField(blank=False, null=False)
    secret = models.TextField(blank=False, null=False)
    password = models.TextField(blank=True, null=True)
    is_sandbox = models.BooleanField(default=False)
    class Meta:
        managed = True
        db_table = 'tb_user_trading_config'
