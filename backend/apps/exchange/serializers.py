from rest_framework import serializers
from .models import ExchangeAccount


class ExchangeAccountSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=True)
    api_secret = serializers.CharField(write_only=True, required=True)
    testnet = serializers.BooleanField(write_only=True, default=False)
    leverage = serializers.IntegerField(write_only=True, default=10)

    class Meta:
        model = ExchangeAccount
        fields = ['id', 'exchange', 'label', 'is_active', 'created_at',
                  'api_key', 'api_secret', 'testnet', 'leverage']
        read_only_fields = ['id', 'created_at']

    def create(self, validated_data):
        validated_data.pop('testnet', None)
        validated_data.pop('leverage', None)
        api_key = validated_data.pop('api_key')
        api_secret = validated_data.pop('api_secret')
        account = ExchangeAccount(**validated_data)
        account.api_key_enc = account.encrypt_api_key(api_key)
        account.api_secret_enc = account.encrypt_api_secret(api_secret)
        account.save()
        return account
