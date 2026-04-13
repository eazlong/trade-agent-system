import uuid
from django.conf import settings
from django.db import models


class ExchangeAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exchange = models.CharField(max_length=32)  # binance / okx / bybit
    label = models.CharField(max_length=64, blank=True)
    api_key_enc = models.BinaryField()   # Fernet加密
    api_secret_enc = models.BinaryField()
    is_active = models.BooleanField(default=True)
    testnet = models.BooleanField(default=False, help_text='是否为模拟账户')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'exchange_accounts'

    def __str__(self):
        return f'{self.exchange} ({self.label})'

    def _get_fernet(self):
        from cryptography.fernet import Fernet
        key = getattr(settings, 'FERNET_KEY', '')
        if not key:
            raise ValueError('FERNET_KEY not configured')
        return Fernet(key.encode())

    def encrypt_api_key(self, value: str) -> bytes:
        return self._get_fernet().encrypt(value.encode())

    def encrypt_api_secret(self, value: str) -> bytes:
        return self._get_fernet().encrypt(value.encode())

    def decrypt_api_key(self) -> str:
        return self._get_fernet().decrypt(bytes(self.api_key_enc)).decode()

    def decrypt_api_secret(self) -> str:
        return self._get_fernet().decrypt(bytes(self.api_secret_enc)).decode()
