import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='AgentMemory',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('agent_type', models.CharField(max_length=16, choices=[
                    ('supervisor', 'SupervisorAgent'),
                    ('analyst', 'AnalystAgent'),
                    ('quant', 'QuantEngineerAgent'),
                    ('coach', 'CoachAgent'),
                    ('risk_advisor', 'RiskAdvisorAgent'),
                ])),
                ('content', models.TextField()),
                ('metadata', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'agent_memory'},
        ),
        migrations.AddIndex(
            model_name='agentmemory',
            index=models.Index(fields=['agent_type', '-created_at'], name='agent_memory_type_idx'),
        ),
        migrations.CreateModel(
            name='AgentAuditLog',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('agent_type', models.CharField(max_length=16)),
                ('action', models.CharField(max_length=64)),
                ('input_summary', models.TextField(blank=True)),
                ('output_summary', models.TextField(blank=True)),
                ('token_used', models.IntegerField(default=0)),
                ('duration_ms', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'agent_audit_logs'},
        ),
        migrations.AddIndex(
            model_name='agentauditlog',
            index=models.Index(fields=['-created_at'], name='agent_audit_created_89d8b0_idx'),
        ),
    ]
