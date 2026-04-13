from rest_framework import serializers
from .models import BacktestResult, BacktestTrade


class BacktestResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = BacktestResult
        fields = [
            'id', 'strategy', 'symbol', 'timeframe', 'start_date', 'end_date',
            'initial_capital', 'final_capital', 'total_return_pct',
            'sharpe_ratio', 'max_drawdown_pct', 'win_rate', 'total_trades',
            'git_commit_hash', 'parameters', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class BacktestDetailSerializer(serializers.ModelSerializer):
    """Extended serializer with equity/drawdown curves, OHLCV data, and indicators."""

    class Meta:
        model = BacktestResult
        fields = [
            'id', 'strategy', 'symbol', 'timeframe', 'start_date', 'end_date',
            'initial_capital', 'final_capital', 'total_return_pct',
            'sharpe_ratio', 'max_drawdown_pct', 'win_rate', 'total_trades',
            'git_commit_hash', 'parameters', 'created_at',
            'equity_curve', 'drawdown_curve', 'ohlcv_data', 'indicator_data',
        ]
        read_only_fields = ['id', 'created_at']


class BacktestTradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BacktestTrade
        fields = [
            'id', 'entry_time', 'exit_time', 'symbol', 'side',
            'entry_price', 'exit_price', 'quantity', 'pnl',
            'pnl_pct', 'cumulative_pnl', 'fees', 'tags',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']
