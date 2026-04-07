"""
DataSource Application

交易所数据源对接模块，支持：
- 加密货币交易所（Binance/OKX/Bybit）
- 股票市场（美股/A股/港股）

数据流程:
交易所 → WebSocket/REST → 数据清洗标准化 → 内存存储 → API接口 → 过期删除(4h)
"""

default_app_config = 'apps.datasource.apps.DataSourceConfig'