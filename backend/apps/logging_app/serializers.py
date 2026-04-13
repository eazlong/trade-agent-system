from rest_framework import serializers
from apps.logging_app.models import SystemLog


class SystemLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemLog
        fields = [
            "id",
            "level",
            "module",
            "logger_name",
            "message",
            "trace_id",
            "extra_data",
            "created_at",
        ]
        read_only_fields = fields
