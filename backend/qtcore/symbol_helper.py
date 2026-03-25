import requests, os
from utils.redis_cache import redis_cache
from utils.singleton import singleton
import re
import logging

# API 配置
API_KEY = os.getenv("COINMARKETCAP_API_KEY", "f2dfe4ae-0671-4554-9a2e-965f5824b6cd") 

@singleton
class SymbolHelper(object):
    base_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/"
    # def __init__(self):
    #     self.base_url = ""

    def _request(self, url, symbol):
        params = {"symbol": symbol, "convert": "USD"}
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": API_KEY}
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            logging.error(f"请求币种信息错误：{response.json}")
            raise Exception("请求币种信息错误")


    @redis_cache
    def get_symbol_info(self, symbol):
        symbol = re.sub(r'^\d+|USDT$', "", symbol)
        url = f"{self.base_url}quotes/latest"
        data = self._request(url,symbol)
        return data['data'][symbol]
    