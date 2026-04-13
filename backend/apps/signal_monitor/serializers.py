"""信号监控序列化器"""

from rest_framework import serializers
from .models import SignalMonitor, SignalTriggerLog


class SignalMonitorCreateSerializer(serializers.Serializer):
    """创建信号监控的序列化器"""

    name = serializers.CharField(max_length=128)
    symbol = serializers.CharField(max_length=32)
    interval = serializers.CharField(max_length=8, default="1h")
    source = serializers.CharField(max_length=32, default="binance")
    indicator_type = serializers.CharField(max_length=32)
    indicator_params = serializers.JSONField(default=dict)
    condition = serializers.JSONField()
    trigger_type = serializers.ChoiceField(
        choices=["once", "continuous"], default="once"
    )
    action_type = serializers.ChoiceField(
        choices=["notify", "trade", "notify_and_trade"],
        default="notify",
    )
    action_params = serializers.JSONField(default=dict)
    backtest_result_id = serializers.UUIDField(required=False, allow_null=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class SignalMonitorUpdateSerializer(serializers.Serializer):
    """更新信号监控的序列化器"""

    name = serializers.CharField(max_length=128, required=False)
    condition = serializers.JSONField(required=False)
    trigger_type = serializers.ChoiceField(
        choices=["once", "continuous"], required=False
    )
    action_type = serializers.ChoiceField(
        choices=["notify", "trade", "notify_and_trade"],
        required=False,
    )
    action_params = serializers.JSONField(required=False)
    status = serializers.ChoiceField(
        choices=["active", "disabled"],
        required=False,
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class SignalMonitorListSerializer(serializers.ModelSerializer):
    """信号监控列表序列化器"""

    class Meta:
        model = SignalMonitor
        fields = [
            "id",
            "name",
            "symbol",
            "interval",
            "source",
            "indicator_type",
            "indicator_params",
            "condition",
            "trigger_type",
            "action_type",
            "action_params",
            "status",
            "last_triggered_at",
            "trigger_count",
            "expires_at",
            "backtest_result",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "last_triggered_at",
            "trigger_count",
            "created_at",
            "updated_at",
        ]


class SignalTriggerLogSerializer(serializers.ModelSerializer):
    """触发日志序列化器"""

    class Meta:
        model = SignalTriggerLog
        fields = ["id", "monitor", "trigger_value", "action_result", "executed_at"]
