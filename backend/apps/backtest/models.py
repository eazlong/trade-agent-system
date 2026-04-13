import uuid
from django.db import models


class BacktestResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    strategy = models.ForeignKey('trading.Strategy', on_delete=models.CASCADE)
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=8)
    start_date = models.DateField()
    end_date = models.DateField()
    initial_capital = models.DecimalField(max_digits=20, decimal_places=2)
    final_capital = models.DecimalField(max_digits=20, decimal_places=2)
    total_return_pct = models.FloatField()
    sharpe_ratio = models.FloatField(null=True)
    max_drawdown_pct = models.FloatField(null=True)
    win_rate = models.FloatField(null=True)
    total_trades = models.IntegerField(default=0)
    git_commit_hash = models.CharField(max_length=40, blank=True)
    parameters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    # Time series data (downsampled to ≤2000 points at write time)
    equity_curve = models.JSONField(
        default=list,
        blank=True,
        help_text='[{"timestamp": "...", "equity": 10000.0, "drawdown": 0.0}, ...]',
    )
    drawdown_curve = models.JSONField(
        default=list,
        blank=True,
        help_text='[{"timestamp": "...", "drawdown": -0.012}, ...]',
    )

    # OHLCV market data used for backtest
    ohlcv_data = models.JSONField(
        default=list,
        blank=True,
        help_text='[{"timestamp": "...", "open": x, "high": x, "low": x, "close": x, "volume": x}, ...]',
    )

    # Computed technical indicators
    indicator_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='{"ma7": [...], "ma25": [...], "macd": {"dif": [...], "dea": [...], "hist": [...]}, "rsi": [...]}',
    )

    class Meta:
        db_table = 'backtest_results'


class BacktestTrade(models.Model):
    """Individual trade record from a backtest run."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    backtest = models.ForeignKey(
        BacktestResult,
        on_delete=models.CASCADE,
        related_name='trades',
    )

    entry_time = models.DateTimeField()
    exit_time = models.DateTimeField(null=True, blank=True)
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=4)  # 'long' or 'short'
    entry_price = models.DecimalField(max_digits=20, decimal_places=8)
    exit_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    pnl = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    pnl_pct = models.FloatField(null=True, blank=True)
    cumulative_pnl = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    fees = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'backtest_trades'
        indexes = [
            models.Index(fields=['backtest', 'entry_time']),
            models.Index(fields=['backtest', '-pnl']),
        ]
        ordering = ['entry_time']
