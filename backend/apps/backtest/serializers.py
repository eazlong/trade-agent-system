from rest_framework import serializers
from .models import BacktestResult, BacktestTrade


class BacktestResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = BacktestResult
        fields = [
            "id",
            "strategy",
            "symbol",
            "timeframe",
            "start_date",
            "end_date",
            "initial_capital",
            "final_capital",
            "total_return_pct",
            "sharpe_ratio",
            "max_drawdown_pct",
            "win_rate",
            "total_trades",
            "git_commit_hash",
            "parameters",
            "review_status",
            "review_notes",
            "reviewed_at",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "review_status", "review_notes", "reviewed_at"]


class BacktestTradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BacktestTrade
        fields = [
            "id",
            "backtest_id",
            "entry_time",
            "exit_time",
            "side",
            "entry_price",
            "exit_price",
            "quantity",
            "pnl",
            "pnl_pct",
            "commission",
            "signal",
            "exit_reason",
        ]
        read_only_fields = ["id"]


class BacktestDetailSerializer(serializers.ModelSerializer):
    """Full detail serializer: summary + equity_curve + drawdown_curve."""

    equity_curve = serializers.JSONField(read_only=True)
    drawdown_curve = serializers.JSONField(read_only=True)

    class Meta:
        model = BacktestResult
        fields = [
            "id",
            "strategy",
            "symbol",
            "timeframe",
            "start_date",
            "end_date",
            "initial_capital",
            "final_capital",
            "total_return_pct",
            "sharpe_ratio",
            "max_drawdown_pct",
            "win_rate",
            "total_trades",
            "git_commit_hash",
            "parameters",
            "equity_curve",
            "drawdown_curve",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
