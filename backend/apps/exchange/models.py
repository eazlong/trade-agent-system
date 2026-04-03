import uuid
from django.db import models
from django.conf import settings


class ExchangeAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exchange = models.CharField(max_length=32)  # binance / okx / bybit
    label = models.CharField(max_length=64, blank=True)
    api_key_enc = models.BinaryField()   # Fernet加密
    api_secret_enc = models.BinaryField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'exchange_accounts'

    def __str__(self):
        return f'{self.exchange} ({self.label})'
