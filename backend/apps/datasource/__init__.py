"""
DataSource Application

交易所数据源对接模块（MVP 仅 Binance）：
- 加密货币交易所（Binance）

数据流程:
交易所 → WebSocket/REST → 数据清洗标准化 → 内存存储 → API接口 → 过期删除(4h)
"""

default_app_config = "apps.datasource.apps.DataSourceConfig"
