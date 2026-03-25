from django.contrib import admin
from .models import (TbUserBotConfig
                     )

@admin.register(TbUserBotConfig)
class TbUserBotConfigAdmin(admin.ModelAdmin):
    list_display = ( 'id', 'expire', 'user_id', 'symbol', 'bot_name', 'params', 'profit')
    # inlines = [
    #     TbNotifyTemplate
    # ]  

# @admin.register(TbUserTradeRecord)
# class TbUserTradeRecordAdmin(admin.ModelAdmin):
#     list_display = ( 'id', 'bot_name', 'user_id', 'side', 'action', 'symbol', 'price', 'amount', 'profit')
#     # inlines = [
#     #     TbNotifyTemplate
#     # ]  



