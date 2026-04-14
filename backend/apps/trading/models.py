import uuid
from django.conf import settings
from django.db import models


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("submitted", "Submitted"),
        ("partial", "Partial"),
        ("filled", "Filled"),
        ("cancelled", "Cancelled"),
        ("failed", "Failed"),
    ]
    SIDE_CHOICES = [("buy", "Buy"), ("sell", "Sell")]
    TYPE_CHOICES = [("market", "Market"), ("limit", "Limit"), ("stop", "Stop")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True, default=uuid.uuid4)  # 幂等键
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True
    )
    exchange_account = models.ForeignKey(
        "exchange.ExchangeAccount", on_delete=models.PROTECT
    )
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    order_type = models.CharField(max_length=8, choices=TYPE_CHOICES)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="pending")
    exchange_order_id = models.CharField(max_length=128, blank=True)
    filled_quantity = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    avg_fill_price = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True
    )
    realized_pnl = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True
    )  # 已实现盈亏
    error_message = models.TextField(blank=True)

    # 关联实盘会话和策略
    live_session = models.ForeignKey(
        "trading.LiveSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        help_text="订单所属的实盘会话",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status"]),
        ]


class Strategy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128)
    code_path = models.CharField(max_length=256)  # strategies/{id}.py
    git_commit_hash = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "strategies"


class DailySnapshot(models.Model):
    """
    每日账户净值快照。
    用于风控回撤计算，OrderExecutor 每日开盘前记录。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date = models.DateField()
    total_equity = models.DecimalField(
        max_digits=20, decimal_places=8
    )  # 期初总权益(USDT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "daily_snapshots"
        unique_together = [["user", "date"]]
        indexes = [
            models.Index(fields=["user", "-date"]),
        ]


class LiveSession(models.Model):
    """
    实盘交易会话。
    从回测审核通过后部署，记录策略运行状态和收益。
    """

    STATUS_CHOICES = [
        ("pending", "待启动"),
        ("running", "运行中"),
        ("paused", "已暂停"),
        ("stopped", "已停止"),
        ("error", "异常"),
    ]
    MODE_CHOICES = [("live", "实盘"), ("paper", "模拟")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    backtest_result = models.ForeignKey(
        "backtest.BacktestResult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="live_sessions",
        help_text="来源回测",
    )
    strategy = models.ForeignKey(Strategy, on_delete=models.PROTECT)
    symbol = models.CharField(max_length=32)
    mode = models.CharField(max_length=8, choices=MODE_CHOICES, default="paper")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="pending")
    exchange_account = models.ForeignKey(
        "exchange.ExchangeAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="绑定的交易所账户",
    )
    initial_capital = models.DecimalField(max_digits=20, decimal_places=2)
    current_equity = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True
    )
    started_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    config = models.JSONField(default=dict, help_text="运行参数（仓位比例、止损等）")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "live_sessions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "mode"]),
            models.Index(fields=["backtest_result"]),
        ]

    def __str__(self):
        return f"LiveSession({self.mode}/{self.status}) - {self.strategy.name} {self.symbol}"
