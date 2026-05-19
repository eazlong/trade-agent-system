import uuid
from django.db import models

# Safe import of pgvector VectorField with TextField fallback
try:
    from pgvector.django import VectorField
except ImportError:
    VectorField = lambda dimensions, **kwargs: models.TextField(  # noqa: E731
        null=True, blank=True, **kwargs
    )


class AgentMemory(models.Model):
    """L3 长期记忆（pgvector存储，语义检索）"""

    AGENT_CHOICES = [
        ("supervisor", "SupervisorAgent"),
        ("analyst", "AnalystAgent"),
        ("quant", "QuantEngineerAgent"),
        ("coach", "CoachAgent"),
        ("risk_advisor", "RiskAdvisorAgent"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_type = models.CharField(max_length=16, choices=AGENT_CHOICES)
    content = models.TextField()
    # embedding vector stored via pgvector (managed via raw SQL migration)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- New fields (P0-04) ---
    user_id = models.CharField(max_length=64, db_index=True, default="")
    agent_name = models.CharField(max_length=32, default="")
    session_id = models.CharField(max_length=64, blank=True, default="")
    embedding = VectorField(dimensions=768, null=True)

    class Meta:
        db_table = "agent_memory"
        indexes = [
            models.Index(
                fields=["agent_type", "-created_at"],
                name="agent_memor_agent_t_ce224a_idx",
            ),
            models.Index(fields=["user_id"], name="agent_memory_user_id_idx"),
        ]


class AgentAuditLog(models.Model):
    """Agent操作审计日志（月分区，仅append）"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_type = models.CharField(max_length=16)
    action = models.CharField(max_length=64)
    input_summary = models.TextField(blank=True)
    output_summary = models.TextField(blank=True)
    token_used = models.IntegerField(default=0)
    duration_ms = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_audit_logs"
        indexes = [
            models.Index(fields=["-created_at"], name="agent_audit_created_89d8b0_idx")
        ]


class TaskProgress(models.Model):
    """Completed/failed task history — running state lives in Redis, archived here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_id = models.CharField(
        max_length=128, unique=True, db_index=True, verbose_name="Task ID"
    )
    user = models.ForeignKey(
        "authentication.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="task_progress",
    )
    task_type = models.CharField(max_length=64)  # backtest, agent, data_fetch
    status = models.CharField(max_length=32)  # completed / failed
    progress = models.FloatField(default=0.0)
    milestones = models.JSONField(default=list, verbose_name="里程碑记录")
    result = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "task_progress"
        ordering = ["-created_at"]

    def __str__(self):
        return f"TaskProgress({self.task_id[:8]}) {self.status}"
