"""Tests for notification module."""
from __future__ import annotations

from django.test import TestCase

from apps.notify.models import Notification


class TestNotificationModel(TestCase):
    """测试 Notification 模型"""

    def setUp(self):
        from apps.authentication.models import User
        self.user = User.objects.create_user(
            email='notif@example.com',
            password='pass123',
            username='notifuser',
        )

    def test_create_notification(self):
        notif = Notification.objects.create(
            user=self.user,
            message='This is a test notification',
            channel='telegram',
        )
        self.assertEqual(notif.channel, 'telegram')
        self.assertFalse(notif.is_read)
        self.assertIsNotNone(notif.created_at)

    def test_mark_as_read(self):
        notif = Notification.objects.create(
            user=self.user,
            message='Read me',
            channel='web',
        )
        notif.is_read = True
        notif.save()
        refreshed = Notification.objects.get(id=notif.id)
        self.assertTrue(refreshed.is_read)

    def test_channel_choices(self):
        for channel in ['telegram', 'web']:
            notif = Notification.objects.create(
                user=self.user,
                message=f'{channel} notification',
                channel=channel,
            )
            self.assertEqual(notif.channel, channel)

    def test_string_representation(self):
        notif = Notification.objects.create(
            user=self.user,
            message='This is a long notification message for testing',
            channel='telegram',
        )
        self.assertIn('[telegram]', str(notif))
        self.assertIn('This is a long', str(notif))

    def test_user_notifications_relation(self):
        """测试用户与通知的反向关系"""
        Notification.objects.create(user=self.user, message='msg1', channel='telegram')
        Notification.objects.create(user=self.user, message='msg2', channel='web')
        self.assertEqual(self.user.notifications.count(), 2)

    def test_ordering_by_created_at(self):
        """测试通知按时间倒序"""
        n1 = Notification.objects.create(user=self.user, message='first', channel='telegram')
        n2 = Notification.objects.create(user=self.user, message='second', channel='telegram')
        all_notifs = list(Notification.objects.filter(user=self.user).order_by('-created_at'))
        self.assertEqual(all_notifs[0].message, 'second')
