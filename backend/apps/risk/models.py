import uuid
from django.db import models


class RiskEvent(models.Model):
    LEVEL_CHOICES = [('P0', 'P0'), ('P1', 'P1'), ('P2', 'P2')]
    TYPE_CHOICES = [
        ('hard_limit', 'Hard Limit'),
        ('circuit_breaker', 'Circuit Breaker'),
        ('reconciliation', 'Reconciliation'),
        ('heartbeat', 'Heartbeat'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES)
    event_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    message = models.TextField()
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'risk_events'
        indexes = [models.Index(fields=['-created_at'])]


class RiskConfig(models.Model):
    daily_loss_warning_pct = models.FloatField(default=0.03)
    consecutive_loss_alert = models.IntegerField(default=3)
    position_suggestion_limit = models.FloatField(default=0.1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'risk_configs'
