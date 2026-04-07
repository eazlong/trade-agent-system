"""
数据源 API 序列化器
"""
from rest_framework import serializers
from datetime import datetime


class KlineRequestSerializer(serializers.Serializer):
    """K线数据请求"""
    source = serializers.CharField(help_text='数据源名称')
    symbol = serializers.CharField(help_text='交易对/股票代码')
    interval = serializers.CharField(help_text='K线周期')
    market_type = serializers.CharField(required=False, default='spot', help_text='市场类型')
    start_time = serializers.DateTimeField(required=False, help_text='开始时间')
    end_time = serializers.DateTimeField(required=False, help_text='结束时间')
    limit = serializers.IntegerField(required=False, default=100, min_value=1, max_value=10000)


class KlineResponseSerializer(serializers.Serializer):
    """K线数据响应"""
    symbol = serializers.CharField()
    interval = serializers.CharField()
    open_time = serializers.DateTimeField()
    close_time = serializers.DateTimeField()
    open = serializers.FloatField()
    high = serializers.FloatField()
    low = serializers.FloatField()
    close = serializers.FloatField()
    volume = serializers.FloatField()
    turnover = serializers.FloatField()
    trades = serializers.IntegerField()
    source = serializers.CharField()
    timestamp = serializers.DateTimeField()


class TickerRequestSerializer(serializers.Serializer):
    """行情数据请求"""
    source = serializers.CharField(help_text='数据源名称')
    symbol = serializers.CharField(help_text='交易对/股票代码')
    market_type = serializers.CharField(required=False, default='spot')


class TickerResponseSerializer(serializers.Serializer):
    """行情数据响应"""
    symbol = serializers.CharField()
    last_price = serializers.FloatField()
    bid_price = serializers.FloatField()
    bid_quantity = serializers.FloatField()
    ask_price = serializers.FloatField()
    ask_quantity = serializers.FloatField()
    high_24h = serializers.FloatField()
    low_24h = serializers.FloatField()
    volume_24h = serializers.FloatField()
    turnover_24h = serializers.FloatField()
    change_24h = serializers.FloatField()
    change_pct_24h = serializers.FloatField()
    timestamp = serializers.DateTimeField()
    source = serializers.CharField()


class TradeRequestSerializer(serializers.Serializer):
    """成交数据请求"""
    source = serializers.CharField()
    symbol = serializers.CharField()
    market_type = serializers.CharField(required=False, default='spot')
    limit = serializers.IntegerField(required=False, default=100)


class TradeResponseSerializer(serializers.Serializer):
    """成交数据响应"""
    symbol = serializers.CharField()
    trade_id = serializers.CharField()
    price = serializers.FloatField()
    quantity = serializers.FloatField()
    side = serializers.CharField()
    timestamp = serializers.DateTimeField()
    source = serializers.CharField()


class SubscriptionRequestSerializer(serializers.Serializer):
    """数据订阅请求"""
    source = serializers.CharField(help_text='数据源名称')
    symbol = serializers.CharField(help_text='交易对/股票代码')
    data_type = serializers.CharField(help_text='数据类型')
    interval = serializers.CharField(required=False, help_text='K线周期')
    market_type = serializers.CharField(required=False, default='spot')


class SubscriptionResponseSerializer(serializers.Serializer):
    """数据订阅响应"""
    subscription_id = serializers.CharField()
    source = serializers.CharField()
    symbol = serializers.CharField()
    data_type = serializers.CharField()
    interval = serializers.CharField(allow_null=True)
    market_type = serializers.CharField()
    active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    last_update_time = serializers.DateTimeField(allow_null=True)
    update_count = serializers.IntegerField()


class DataSourceStatusSerializer(serializers.Serializer):
    """数据源状态"""
    name = serializers.CharField()
    is_loaded = serializers.BooleanField()
    status = serializers.CharField()
    subscription_count = serializers.IntegerField(required=False)
    connected_at = serializers.DateTimeField(required=False, allow_null=True)
    last_data_time = serializers.FloatField(required=False)


class QualityReportSerializer(serializers.Serializer):
    """数据质量报告"""
    source = serializers.CharField()
    symbol = serializers.CharField()
    data_type = serializers.CharField()
    check_time = serializers.DateTimeField()
    completeness_rate = serializers.FloatField()
    accuracy_score = serializers.FloatField()
    avg_latency_ms = serializers.FloatField()
    max_latency_ms = serializers.FloatField()
    latency_violations = serializers.IntegerField()
    status = serializers.CharField()
    issues = serializers.ListField(child=serializers.CharField())