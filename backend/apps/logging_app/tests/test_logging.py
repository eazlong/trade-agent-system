import json
import logging
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.urls import reverse

from apps.logging_app.models import SystemLog
from apps.logging_app.handler import SystemLogHandler
from apps.logging_app.mapper import resolve_module


class ModuleMapperTest(TestCase):
    def test_known_modules(self):
        self.assertEqual(resolve_module("apps.agent.supervisor"), "agent")
        self.assertEqual(resolve_module("apps.trading.executor"), "trading")
        self.assertEqual(resolve_module("apps.riskguard.guard"), "riskguard")
        self.assertEqual(resolve_module("apps.signal_monitor.engine"), "signal_monitor")
        self.assertEqual(resolve_module("apps.memory.manager"), "memory")
        self.assertEqual(resolve_module("apps.channel.telegram"), "channel")
        self.assertEqual(resolve_module("apps.notify.consumers"), "notify")
        self.assertEqual(resolve_module("apps.exchange.binance"), "exchange")
        self.assertEqual(resolve_module("apps.datasource.manager"), "datasource")
        self.assertEqual(resolve_module("apps.authentication.views"), "auth")
        self.assertEqual(resolve_module("apps.backtest.runner"), "backtest")
        self.assertEqual(resolve_module("apps.skill.loader"), "skill")
        self.assertEqual(resolve_module("apps.core.apps"), "core")
        self.assertEqual(resolve_module("core.asgi"), "core")

    def test_unknown_module(self):
        self.assertEqual(resolve_module("some.unknown.package"), "unknown")
        self.assertEqual(resolve_module(""), "unknown")


class SystemLogModelTest(TestCase):
    def test_create_log(self):
        log = SystemLog.objects.create(
            level="INFO",
            module="agent",
            logger_name="apps.agent.base",
            message="Agent started",
            trace_id="abc-123",
            extra_data={"key": "value"},
        )
        self.assertIsNotNone(log.id)
        self.assertEqual(str(log)[:6], "[INFO]")

    def test_ordering(self):
        SystemLog.objects.create(level="INFO", module="agent", message="First")
        import time as _time

        _time.sleep(0.01)
        SystemLog.objects.create(level="ERROR", module="trading", message="Second")
        logs = list(SystemLog.objects.all())
        self.assertEqual(logs[0].message, "Second")
        self.assertEqual(logs[1].message, "First")


class SystemLogHandlerTest(TestCase):
    @patch("apps.logging_app.handler._get_redis")
    def test_handler_emits_log(self, mock_redis):
        mock_redis.return_value = MagicMock()
        SystemLogHandler()
        record = logging.LogRecord(
            name="apps.agent.base",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test log message",
            args=(),
            exc_info=None,
        )
        # Use DBOnlyLogHandler to avoid async thread race in tests
        from apps.logging_app.handler import DBOnlyLogHandler

        db_handler = DBOnlyLogHandler()
        db_handler.emit(record)
        self.assertEqual(
            SystemLog.objects.filter(message__contains="Test log message").count(), 1
        )

    @patch("apps.logging_app.handler._get_redis")
    def test_handler_resolves_module(self, mock_redis):
        mock_redis.return_value = MagicMock()
        from apps.logging_app.handler import DBOnlyLogHandler

        db_handler = DBOnlyLogHandler()
        record = logging.LogRecord(
            name="apps.trading.executor",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Order failed",
            args=(),
            exc_info=None,
        )
        db_handler.emit(record)
        log = SystemLog.objects.get(message__contains="Order failed")
        self.assertEqual(log.module, "trading")
        self.assertEqual(log.level, "ERROR")


class LogAPITest(TestCase):
    def setUp(self):
        self.client = Client()
        SystemLog.objects.create(level="INFO", module="agent", message="Agent started")
        SystemLog.objects.create(
            level="ERROR", module="trading", message="Order failed"
        )
        SystemLog.objects.create(
            level="WARNING", module="riskguard", message="High leverage detected"
        )
        SystemLog.objects.create(
            level="INFO",
            module="agent",
            message="Skill registered",
            trace_id="trace-001",
        )

    def test_list_all_logs(self):
        resp = self.client.get(reverse("log-list"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 4)

    def test_filter_by_level(self):
        resp = self.client.get(reverse("log-list") + "?level=ERROR")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["level"], "ERROR")

    def test_filter_by_module(self):
        resp = self.client.get(reverse("log-list") + "?module=agent")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)

    def test_filter_by_search(self):
        resp = self.client.get(reverse("log-list") + "?search=failed")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)

    def test_filter_by_trace_id(self):
        resp = self.client.get(reverse("log-list") + "?trace_id=trace-001")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)

    def test_test_log_endpoint(self):
        # Patch the logger to use DBOnlyLogHandler (synchronous)
        import logging as py_logging

        test_logger = py_logging.getLogger("apps.test")
        original_handlers = test_logger.handlers[:]
        from apps.logging_app.handler import DBOnlyLogHandler

        handler = DBOnlyLogHandler()
        test_logger.handlers = [handler]
        try:
            resp = self.client.post(
                reverse("log-test"),
                data=json.dumps(
                    {"level": "WARNING", "message": "Test warning", "module": "test"}
                ),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
            self.assertTrue(
                SystemLog.objects.filter(message__contains="Test warning").exists()
            )
        finally:
            test_logger.handlers = original_handlers
