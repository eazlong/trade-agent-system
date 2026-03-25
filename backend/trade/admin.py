from django.contrib import admin
from .models import (TbUserTradingConfig,
                     TbUserTradeRecord,
                     )

@admin.register(TbUserTradingConfig)
class TbUserTradingConfigAdmin(admin.ModelAdmin):
    list_display = [field.name for field in TbUserTradingConfig._meta.fields]
    search_fields = ['user_id']


@admin.register(TbUserTradeRecord)
class TbUserTradeRecordAdmin(admin.ModelAdmin):
    list_display = ( 'id', 'bot_name', 'user_id', 'side', 'action', 'symbol', 'price', 'amount', 'profit')
    # inlines = [
    #     TbNotifyTemplate
    # ]  