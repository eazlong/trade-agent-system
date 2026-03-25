from django.db import models
from utils.base_model import BaseModel

class TbUserTradeRecord(BaseModel):
    user_id = models.IntegerField(null=False)
    bot_name = models.CharField(max_length=64)
    bot_id = models.IntegerField(null=False, default=1)
    symbol = models.TextField()
    price = models.DecimalField(max_digits=20, decimal_places=8, blank=True, null=True)
    action = models.TextField(blank=True, null=True)
    amount = models.DecimalField(max_digits=20, decimal_places=5, blank=True, null=True)
    side = models.TextField(blank=True, null=True)
    profit = models.DecimalField(max_digits=20, decimal_places=5, default=0.0)
    order_id = models.CharField(max_length=64, default="")
    parent_order_id = models.CharField(max_length=64, default="")
    unfilled_amount = models.DecimalField(max_digits=20, decimal_places=5, default=0.0)
    timestamp = models.DateTimeField(blank=True, null=True)
    profit_pct = models.DecimalField(max_digits=20, decimal_places=5, default=0.0)
    leverage = models.DecimalField(max_digits=20, decimal_places=5, default=0.0)
    
    class Meta:
        managed = True
        db_table = 'tb_user_trade_record'


class TradeRecordComment(BaseModel):
    user_id = models.IntegerField(null=False)
    trade_record_id = models.IntegerField(null=False)
    content = models.TextField(null=False)
    is_public = models.BooleanField(default=False)
    parent_comment_id = models.IntegerField(null=True, blank=True)
    username = models.CharField(max_length=64, blank=True, null=True)
    
    class Meta:
        managed = True
        db_table = 'tb_trade_record_comment'
