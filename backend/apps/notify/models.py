import uuid
from django.conf import settings
from django.db import models


class Notification(models.Model):
    CHANNEL_CHOICES = [("telegram", "Telegram"), ("web", "Web")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    channel = models.CharField(
        max_length=16, choices=CHANNEL_CHOICES, default="telegram"
    )
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications"
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"[{self.channel}] {self.message[:40]}"
