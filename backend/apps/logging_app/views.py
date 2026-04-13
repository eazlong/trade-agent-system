"""
Log API endpoints:
- GET /api/logs/ — query logs with filters (level, module, time range, search)
- GET /api/logs/stream/ — SSE real-time log streaming
- POST /api/logs/test/ — write a test log entry (for testing)
"""

import json
import time

import redis
from django.conf import settings
from django.http import StreamingHttpResponse
from django.views.decorators.gzip import gzip_page
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.logging_app.models import SystemLog
from apps.logging_app.serializers import SystemLogSerializer

LOG_STREAM_KEY = "system:logs"


class LogListView(generics.ListAPIView):
    """Query logs with filters: level, module, search, time range, pagination."""

    serializer_class = SystemLogSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = SystemLog.objects.all()

        level = self.request.query_params.get("level")
        if level:
            qs = qs.filter(level=level.upper())

        module = self.request.query_params.get("module")
        if module:
            qs = qs.filter(module=module)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(message__icontains=search)

        trace_id = self.request.query_params.get("trace_id")
        if trace_id:
            qs = qs.filter(trace_id=trace_id)

        since = self.request.query_params.get("since")
        if since:
            try:
                from django.utils.dateparse import parse_datetime

                dt = parse_datetime(since)
                if dt:
                    qs = qs.filter(created_at__gte=dt)
            except Exception:
                pass

        before = self.request.query_params.get("before")
        if before:
            try:
                from django.utils.dateparse import parse_datetime

                dt = parse_datetime(before)
                if dt:
                    qs = qs.filter(created_at__lte=dt)
            except Exception:
                pass

        return qs.order_by("-created_at")


@api_view(["POST"])
@permission_classes([AllowAny])
def log_test_view(request):
    """Write a test log entry. Useful for testing the logging pipeline."""
    import logging

    level = request.data.get("level", "INFO").upper()
    message = request.data.get("message", "Test log entry")
    module = request.data.get("module", "test")

    numeric_level = getattr(logging, level, logging.INFO)
    logger = logging.getLogger(f"apps.{module}")
    logger.log(numeric_level, message)

    return Response(
        {"status": "ok", "message": "Test log written"}, status=status.HTTP_200_OK
    )


def _sse_event_reader(stream_key, last_id=None):
    """Generator that yields SSE events from Redis Stream."""
    try:
        r = redis.Redis.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379"),
            decode_responses=True,
        )
    except Exception:
        return

    # Ensure stream exists
    if not r.exists(stream_key):
        r.xadd(
            stream_key,
            {
                "level": "INFO",
                "module": "core",
                "logger": "system",
                "message": "SSE stream connected",
                "trace_id": "",
                "extra": "{}",
                "ts": time.time(),
            },
            maxlen=10000,
            approximate=True,
        )

    consumer_name = f"sse-{int(time.time() * 1000)}"
    group_name = "log_sse_group"

    try:
        r.xgroup_create(stream_key, group_name, id=last_id or "0", mkstream=True)
    except redis.ResponseError:
        pass  # group already exists

    try:
        while True:
            try:
                messages = r.xreadgroup(
                    group_name,
                    consumer_name,
                    {stream_key: ">"},
                    count=50,
                    block=30000,  # 30s block
                )
                if messages:
                    for _, entries in messages:
                        for entry_id, fields in entries:
                            data = {
                                "id": entry_id,
                                "level": fields.get("level", "INFO"),
                                "module": fields.get("module", "unknown"),
                                "logger": fields.get("logger", ""),
                                "message": fields.get("message", ""),
                                "trace_id": fields.get("trace_id", ""),
                                "ts": fields.get("ts", ""),
                            }
                            extra = fields.get("extra", "{}")
                            try:
                                data["extra_data"] = (
                                    json.loads(extra)
                                    if isinstance(extra, str)
                                    else extra
                                )
                            except Exception:
                                data["extra_data"] = {}

                            yield f"data: {json.dumps(data)}\n\n"
            except redis.TimeoutError:
                # Send keepalive
                yield ": keepalive\n\n"
            except Exception:
                yield ": error\n\n"
                break
    finally:
        try:
            r.xgroup_delconsumer(stream_key, group_name, consumer_name)
        except Exception:
            pass


@gzip_page
def log_stream_view(request):
    """SSE endpoint for real-time log streaming.
    Optional query param: level=WARNING to filter by minimum level.
    """
    min_level = request.GET.get("level", "").upper()
    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    min_rank = level_order.get(min_level, 0)

    def event_stream():
        yield ":\n\n"  # SSE connection established
        for raw in _sse_event_reader(LOG_STREAM_KEY):
            if raw.startswith(":"):
                yield raw  # keepalive / control
                continue
            try:
                data = json.loads(raw[5:].strip())  # strip "data: " prefix
                msg_rank = level_order.get(data.get("level", "INFO"), 1)
                if msg_rank >= min_rank:
                    yield raw
            except Exception:
                yield raw

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
