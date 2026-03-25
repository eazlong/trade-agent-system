from django.db import models
from utils.base_model import BaseModel

class TbStrategyTemplate(BaseModel):
    name = models.CharField(max_length=255, blank=True, null=True)
    content = models.CharField(max_length=1024, blank=True, null=True)
    params = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'tb_strategy_template'
