"""
股票数据源模块

提供美股、A股、港股的数据接口框架。
实际接入需要对接具体的数据服务商 API（如 Yahoo Finance、Alpha Vantage、东方财富等）。
"""
from .us_stock import USStockDataSource
from .cn_stock import CNStockDataSource
from .hk_stock import HKStockDataSource

__all__ = ['USStockDataSource', 'CNStockDataSource', 'HKStockDataSource']