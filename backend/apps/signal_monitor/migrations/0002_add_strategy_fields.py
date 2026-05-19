# Generated manually — add strategy fields

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("signal_monitor", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="signalmonitor",
            name="action_type",
            field=models.CharField(
                choices=[
                    ("notify", "发送通知"),
                    ("trade", "执行交易"),
                    ("notify_and_trade", "通知并交易"),
                    ("validate_strategy", "策略验证"),
                ],
                default="notify",
                max_length=20,
                verbose_name="动作类型",
            ),
        ),
        migrations.AddField(
            model_name="signalmonitor",
            name="strategy_name",
            field=models.CharField(
                blank=True,
                default="",
                max_length=128,
                verbose_name="策略名称",
            ),
        ),
        migrations.AddField(
            model_name="signalmonitor",
            name="live_session_id",
            field=models.CharField(
                blank=True,
                default="",
                max_length=64,
                verbose_name="实盘会话 ID",
            ),
        ),
        migrations.AddIndex(
            model_name="signalmonitor",
            index=models.Index(
                fields=["strategy_name", "live_session_id"],
                name="signal_moni_strateg_c4e166_idx",
            ),
        ),
    ]
