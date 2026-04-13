import uuid
from django.db import models


class RiskEvent(models.Model):
    LEVEL_CHOICES = [("P0", "P0"), ("P1", "P1"), ("P2", "P2")]
    TYPE_CHOICES = [
        ("hard_limit", "Hard Limit"),
        ("circuit_breaker", "Circuit Breaker"),
        ("reconciliation", "Reconciliation"),
        ("heartbeat", "Heartbeat"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES)
    event_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    message = models.TextField()
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "risk_events"
        indexes = [models.Index(fields=["-created_at"])]


class RiskConfig(models.Model):
    # 原有字段
    daily_loss_warning_pct = models.FloatField(default=0.03)
    consecutive_loss_alert = models.IntegerField(default=3)
    position_suggestion_limit = models.FloatField(default=0.1)
    # 前端风控 tab 所需字段
    max_position_pct = models.FloatField(default=35.0, help_text="最大仓位集中度 %")
    max_drawdown_pct = models.FloatField(default=8.0, help_text="最大回撤限额 %")
    var_limit_pct = models.FloatField(default=3.0, help_text="日 VaR 限额 %")
    stop_loss_pct = models.FloatField(default=2.0, help_text="止损阈值 %")
    auto_stop = models.BooleanField(default=True, help_text="触发风控条件时自动平仓")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "risk_configs"
