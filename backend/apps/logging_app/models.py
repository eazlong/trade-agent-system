import uuid
from django.db import models


class SystemLog(models.Model):
    """统一系统日志（所有模块日志集中存储）"""

    LEVEL_CHOICES = [
        ("DEBUG", "Debug"),
        ("INFO", "Info"),
        ("WARNING", "Warning"),
        ("ERROR", "Error"),
        ("CRITICAL", "Critical"),
    ]
    MODULE_CHOICES = [
        ("agent", "Agent"),
        ("trading", "Trading"),
        ("riskguard", "RiskGuard"),
        ("signal_monitor", "SignalMonitor"),
        ("memory", "Memory"),
        ("channel", "Channel"),
        ("notify", "Notify"),
        ("exchange", "Exchange"),
        ("datasource", "DataSource"),
        ("auth", "Authentication"),
        ("backtest", "Backtest"),
        ("skill", "Skill"),
        ("core", "Core"),
        ("unknown", "Unknown"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES)
    module = models.CharField(max_length=32, choices=MODULE_CHOICES, default="unknown")
    logger_name = models.CharField(max_length=256, blank=True)
    message = models.TextField()
    trace_id = models.CharField(max_length=64, blank=True, db_index=True)
    extra_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "system_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["level", "-created_at"], name="sys_log_level_created_idx"
            ),
            models.Index(
                fields=["module", "-created_at"], name="sys_log_module_created_idx"
            ),
            models.Index(fields=["-created_at"], name="sys_log_created_idx"),
        ]

    def __str__(self):
        return f"[{self.level}] {self.module}: {self.message[:80]}"
