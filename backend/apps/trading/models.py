import uuid
from django.conf import settings
from django.db import models


class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('submitted', 'Submitted'),
        ('partial', 'Partial'),
        ('filled', 'Filled'),
        ('cancelled', 'Cancelled'),
        ('failed', 'Failed'),
    ]
    SIDE_CHOICES = [('buy', 'Buy'), ('sell', 'Sell')]
    TYPE_CHOICES = [('market', 'Market'), ('limit', 'Limit'), ('stop', 'Stop')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True, default=uuid.uuid4)  # 幂等键
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    exchange_account = models.ForeignKey('exchange.ExchangeAccount', on_delete=models.PROTECT)
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    order_type = models.CharField(max_length=8, choices=TYPE_CHOICES)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    exchange_order_id = models.CharField(max_length=128, blank=True)
    filled_quantity = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    avg_fill_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    realized_pnl = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)  # 已实现盈亏
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'orders'
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['status']),
        ]


class Strategy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128)
    code_path = models.CharField(max_length=256)  # strategies/{id}.py
    git_commit_hash = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'strategies'


class DailySnapshot(models.Model):
    """
    每日账户净值快照。
    用于风控回撤计算，OrderExecutor 每日开盘前记录。
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date = models.DateField()
    total_equity = models.DecimalField(max_digits=20, decimal_places=8)  # 期初总权益(USDT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'daily_snapshots'
        unique_together = [['user', 'date']]
        indexes = [
            models.Index(fields=['user', '-date']),
        ]
