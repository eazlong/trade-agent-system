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

    class Meta:
        db_table = 'backtest_results'
