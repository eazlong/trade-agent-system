# Generated migration

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('backtest', '0001_initial'),
    ]

    operations = [
        # Add equity_curve and drawdown_curve JSONFields to BacktestResult
        migrations.AddField(
            model_name='backtestresult',
            name='equity_curve',
            field=models.JSONField(default=list, blank=True, help_text='[{"timestamp": "...", "equity": 10000.0, "drawdown": 0.0}, ...]'),
        ),
        migrations.AddField(
            model_name='backtestresult',
            name='drawdown_curve',
            field=models.JSONField(default=list, blank=True, help_text='[{"timestamp": "...", "drawdown": -0.012}, ...]'),
        ),
        # Create BacktestTrade table
        migrations.CreateModel(
            name='BacktestTrade',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)),
                ('entry_time', models.DateTimeField()),
                ('exit_time', models.DateTimeField(null=True, blank=True)),
                ('symbol', models.CharField(max_length=32)),
                ('side', models.CharField(max_length=4)),
                ('entry_price', models.DecimalField(max_digits=20, decimal_places=8)),
                ('exit_price', models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)),
                ('quantity', models.DecimalField(max_digits=20, decimal_places=8)),
                ('pnl', models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)),
                ('pnl_pct', models.FloatField(null=True, blank=True)),
                ('cumulative_pnl', models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)),
                ('fees', models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)),
                ('tags', models.JSONField(default=list, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('backtest', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='trades', to='backtest.backtestresult')),
            ],
            options={
                'db_table': 'backtest_trades',
                'ordering': ['entry_time'],
            },
        ),
        migrations.AddIndex(
            model_name='backtesttrade',
            index=models.Index(fields=['backtest', 'entry_time'], name='backtest_tr_bt_id_e7a3c1_idx'),
        ),
        migrations.AddIndex(
            model_name='backtesttrade',
            index=models.Index(fields=['backtest', 'pnl'], name='backtest_tr_bt_id_9f2b4c_idx'),
        ),
    ]
