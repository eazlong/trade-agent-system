from rest_framework import serializers
from .models import RiskEvent, RiskConfig


class RiskEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskEvent
        fields = ['id', 'level', 'event_type', 'message', 'resolved', 'created_at', 'resolved_at']
        read_only_fields = ['id', 'created_at']


class RiskConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskConfig
        fields = [
            'daily_loss_warning_pct', 'consecutive_loss_alert',
            'position_suggestion_limit', 'updated_at',
            'max_position_pct', 'max_drawdown_pct', 'var_limit_pct',
            'stop_loss_pct', 'auto_stop',
        ]
