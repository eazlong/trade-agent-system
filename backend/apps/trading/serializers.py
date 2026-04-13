from rest_framework import serializers
from .models import Order, Strategy


class OrderSerializer(serializers.ModelSerializer):
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
