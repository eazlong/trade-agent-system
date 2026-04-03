import uuid
from django.db import models


class AgentMemory(models.Model):
    """L3 长期记忆（pgvector存储，语义检索）"""
    AGENT_CHOICES = [
        ('supervisor', 'SupervisorAgent'),
        ('analyst', 'AnalystAgent'),
        ('quant', 'QuantEngineerAgent'),
        ('coach', 'CoachAgent'),
        ('risk_advisor', 'RiskAdvisorAgent'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_type = models.CharField(max_length=16, choices=AGENT_CHOICES)
    content = models.TextField()
    # embedding vector stored via pgvector (managed via raw SQL migration)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'agent_memory'
        indexes = [models.Index(fields=['agent_type', '-created_at'], name='agent_memor_agent_t_ce224a_idx')]


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
        db_table = 'agent_audit_logs'
        indexes = [models.Index(fields=['-created_at'], name='agent_audit_created_89d8b0_idx')]
