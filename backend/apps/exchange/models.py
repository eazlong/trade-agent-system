import uuid
from django.conf import settings
from django.db import models


class ExchangeAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exchange = models.CharField(max_length=32)  # binance (MVP)
    label = models.CharField(max_length=64, blank=True)

    # 「这个账户是谁的」。**可空**，且是 `SET_NULL` 而不是 `CASCADE`：删一个用户不该
    # 连着把账户与它的密钥一起删掉——账户上挂着订单与实盘会话的历史，密钥删了那些历史
    # 就再也对不上交易所了。用户走了，账户留下，只是「未归属」。
    #
    # **为什么第②段才加**：减仓要按账户通知「受影响的人」（CONTEXT.md:66），而在这之前
    # 账户与人的关系只能顺着「会话 → 用户」反查（`LiveSession.user_id`）。那条路在会话
    # 停止后就断了，而减仓恰恰发生在最需要通知的时刻。所以归属必须直接落在账户上。
    #
    # 空值不是「没有主人」，是**不知道主人是谁**。两条路径都会留下空值：回填时同一个
    # 账户被多个用户用过（填谁都错，见 `0004` 的计数记录）、以及新账户由管理员建而
    # 未指定归属。读取方（`reduce_run` 的通知解析）必须把空值当成「发给全体 is_active
    # 用户」而不是「没有人要通知」——后者的表现是一条告警静悄悄地没有收件人。
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exchange_accounts",
        help_text="账户归属人（空 = 未归属，通知按全体 is_active 发）",
    )
    api_key_enc = models.BinaryField()  # Fernet加密
    api_secret_enc = models.BinaryField()
    is_active = models.BooleanField(default=True)
    testnet = models.BooleanField(default=False, help_text="是否为模拟账户")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "exchange_accounts"

    def __str__(self):
        return f"{self.exchange} ({self.label})"

    def _get_fernet(self):
        from cryptography.fernet import Fernet

        key = getattr(settings, "FERNET_KEY", "")
        if not key:
            raise ValueError("FERNET_KEY not configured")
        return Fernet(key.encode())

    def encrypt_api_key(self, value: str) -> bytes:
        return self._get_fernet().encrypt(value.encode())

    def encrypt_api_secret(self, value: str) -> bytes:
        return self._get_fernet().encrypt(value.encode())

    def decrypt_api_key(self) -> str:
        return self._get_fernet().decrypt(bytes(self.api_key_enc)).decode()

    def decrypt_api_secret(self) -> str:
        return self._get_fernet().decrypt(bytes(self.api_secret_enc)).decode()
