"""Tests for risk module."""
from __future__ import annotations

from django.test import TestCase

from apps.risk.models import RiskEvent, RiskConfig


class TestRiskEventModel(TestCase):
    """测试风险事件模型"""

    def test_create_risk_event(self):
        event = RiskEvent.objects.create(
            level='P0',
            event_type='hard_limit',
            message='Daily loss limit exceeded',
        )
        self.assertEqual(event.level, 'P0')
        self.assertEqual(event.event_type, 'hard_limit')
        self.assertFalse(event.resolved)
        self.assertIsNotNone(event.created_at)
        self.assertIsNone(event.resolved_at)

    def test_risk_event_levels(self):
        for level in ['P0', 'P1', 'P2']:
            event = RiskEvent.objects.create(
                level=level,
                event_type='heartbeat',
                message=f'{level} event',
            )
            self.assertEqual(event.level, level)

    def test_risk_event_types(self):
        for etype in ['hard_limit', 'circuit_breaker', 'reconciliation', 'heartbeat']:
            event = RiskEvent.objects.create(
                level='P1',
                event_type=etype,
                message=f'{etype} test',
            )
            self.assertEqual(event.event_type, etype)

    def test_resolve_event(self):
        from django.utils import timezone
        event = RiskEvent.objects.create(
            level='P0',
            event_type='hard_limit',
            message='Needs resolution',
        )
        event.resolved = True
        event.resolved_at = timezone.now()
        event.save()
        refreshed = RiskEvent.objects.get(id=event.id)
        self.assertTrue(refreshed.resolved)
        self.assertIsNotNone(refreshed.resolved_at)

    def test_ordering_by_created_at(self):
        """测试风险事件按时间倒序索引"""
        RiskEvent.objects.create(level='P2', event_type='heartbeat', message='first')
        RiskEvent.objects.create(level='P1', event_type='hard_limit', message='second')
        events = list(RiskEvent.objects.order_by('-created_at'))
        self.assertEqual(events[0].message, 'second')


class TestRiskConfigModel(TestCase):
    """测试风险配置模型"""

    def test_default_values(self):
        config = RiskConfig.objects.create()
        self.assertEqual(config.daily_loss_warning_pct, 0.03)
        self.assertEqual(config.consecutive_loss_alert, 3)
        self.assertEqual(config.position_suggestion_limit, 0.1)

    def test_update_config(self):
        config = RiskConfig.objects.create(
            daily_loss_warning_pct=0.05,
            consecutive_loss_alert=5,
            position_suggestion_limit=0.2,
        )
        config.daily_loss_warning_pct = 0.08
        config.save()
        refreshed = RiskConfig.objects.get(id=config.id)
        self.assertEqual(refreshed.daily_loss_warning_pct, 0.08)

    def test_auto_updated_at(self):
        import time
        config = RiskConfig.objects.create()
        first_updated = config.updated_at
        time.sleep(0.01)
        config.daily_loss_warning_pct = 0.1
        config.save()
        refreshed = RiskConfig.objects.get(id=config.id)
        self.assertGreater(refreshed.updated_at, first_updated)
