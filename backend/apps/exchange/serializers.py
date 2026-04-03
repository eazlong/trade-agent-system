from rest_framework import serializers
from .models import ExchangeAccount


class ExchangeAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExchangeAccount
        fields = ['id', 'exchange', 'label', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']
