from django.db import models
from utils.base_model import BaseModel

class TbUserBotConfig(BaseModel):
    symbol = models.CharField(max_length=255)
    repeat = models.IntegerField(blank=True, null=True)
    expire = models.DateTimeField(blank=True, null=True)
    user_id = models.IntegerField(blank=True, null=True)
    template_id = models.IntegerField(blank=True, null=True)
    paused = models.BooleanField(default=False)
    usdt = models.IntegerField()
    bot_name = models.CharField(max_length=64)
    params = models.CharField(max_length=1204)
    profit = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    deleted = models.BooleanField(default=False)
    
    position_amount = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    position_side = models.TextField(blank=True, null=True)
    unrealized_profit = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    liquidation_price = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    
    class Meta:
        managed = True
        db_table = 'tb_user_bot_config'
        # unique_together = (('user_id', 'template_id'),)
