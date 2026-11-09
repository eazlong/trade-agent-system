import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, username, password, **extra_fields):
        """建普通用户。`**extra_fields` 只为测试/脚本能带上偏好字段（如
        `header_symbols`）；生产入口（注册、登录）只走三个位置参数。"""
        extra_fields.setdefault("is_admin", False)
        user = self.model(
            email=self.normalize_email(email),
            username=username,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, username, password):
        user = self.create_user(email, username, password)
        user.is_admin = True
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=64, unique=True)
    telegram_id = models.CharField(
        max_length=64, null=True, blank=True, verbose_name="Telegram User ID"
    )
    telegram_chat_id = models.BigIntegerField(
        null=True, blank=True, verbose_name="Telegram Chat ID"
    )
    feishu_open_id = models.CharField(
        max_length=128, null=True, blank=True, verbose_name="Feishu Open ID"
    )
    header_symbols = models.JSONField(
        default=None,
        null=True,
        blank=True,
        help_text=(
            '顶栏行情币种清单（3–5 个，如 ["BTC/USDT", "ETH/USDT"]）。'
            "null = 用默认四档。校验与归一在 apps/authentication/preferences.py。"
        ),
    )
    is_active = models.BooleanField(default=True)
    is_frozen = models.BooleanField(default=False)  # 风控冻结
    is_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    objects = UserManager()

    class Meta:
        db_table = "users"

    def __str__(self):
        return self.username

    def has_perm(self, perm, obj=None):
        return True

    def has_module_perms(self, app_label):
        return True

    @property
    def is_staff(self):
        return self.is_admin
