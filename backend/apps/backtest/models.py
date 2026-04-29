import uuid
from django.conf import settings
from django.db import models


class BacktestResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    strategy = models.ForeignKey("trading.Strategy", on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="backtest_results",
        help_text="发起回测的用户",
    )
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

    # 审核字段
    REVIEW_STATUS_CHOICES = [
        ("pending", "待审核"),
        ("approved", "审核通过"),
        ("rejected", "审核不通过"),
    ]
    review_status = models.CharField(
        max_length=12,
        choices=REVIEW_STATUS_CHOICES,
        default="pending",
        help_text="回测审核状态",
    )
    review_notes = models.TextField(
        blank=True,
        help_text="审核备注",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="审核时间",
    )

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
        db_table = "backtest_results"


class BacktestTrade(models.Model):
    """Individual trade record within a backtest."""

    backtest = models.ForeignKey(
        BacktestResult, on_delete=models.CASCADE, related_name="trades"
    )
    entry_time = models.DateTimeField()
    exit_time = models.DateTimeField(null=True, blank=True)
    side = models.CharField(
        max_length=5,
        choices=[("long", "Long"), ("short", "Short")],
    )
    entry_price = models.DecimalField(max_digits=20, decimal_places=8)
    exit_price = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True
    )
    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    pnl = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    pnl_pct = models.FloatField(null=True, blank=True)
    commission = models.DecimalField(
        max_digits=20, decimal_places=8, default=0, blank=True
    )
    signal = models.CharField(
        max_length=64,
        blank=True,
        help_text="What signal triggered this trade",
    )
    exit_reason = models.CharField(
        max_length=64,
        blank=True,
        help_text="Why this trade was closed (stop_loss, take_profit, signal, etc.)",
    )

    class Meta:
        db_table = "backtest_trades"
        ordering = ["entry_time"]
