from rest_framework import serializers
from .models import BacktestResult


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
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
