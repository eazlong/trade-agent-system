from rest_framework import serializers
from .models import Order, Strategy, LiveSession


class OrderSerializer(serializers.ModelSerializer):
    live_session_id = serializers.UUIDField(
        source="live_session.id", read_only=True, allow_null=True
    )

    class Meta:
        model = Order
        fields = [
            "id",
            "request_id",
            "exchange_account",
            "symbol",
            "side",
            "order_type",
            "quantity",
            "price",
            "status",
            "exchange_order_id",
            "filled_quantity",
            "avg_fill_price",
            "error_message",
            "live_session_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class StrategySerializer(serializers.ModelSerializer):
    class Meta:
        model = Strategy
        fields = [
            "id",
            "name",
            "code_path",
            "git_commit_hash",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class LiveSessionSerializer(serializers.ModelSerializer):
    strategy_name = serializers.CharField(source="strategy.name", read_only=True)
    backtest_result_id = serializers.UUIDField(
        source="backtest_result.id", read_only=True, allow_null=True
    )
    exchange_account_name = serializers.CharField(
        source="exchange_account.label", read_only=True, allow_null=True
    )

    class Meta:
        model = LiveSession
        fields = [
            "id",
            "user",
            "strategy",
            "strategy_name",
            "backtest_result",
            "backtest_result_id",
            "symbol",
            "mode",
            "status",
            "exchange_account",
            "exchange_account_name",
            "initial_capital",
            "current_equity",
            "config",
            "started_at",
            "stopped_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "started_at",
            "stopped_at",
        ]
