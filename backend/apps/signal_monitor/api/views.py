"""信号监控 API 视图"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from ..models import SignalMonitor, SignalTriggerLog
from ..serializers import (
    SignalMonitorCreateSerializer,
    SignalMonitorUpdateSerializer,
    SignalMonitorListSerializer,
    SignalTriggerLogSerializer,
)


class SignalMonitorListView(APIView):
    """信号监控列表"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """获取用户的信号监控列表"""
        monitors = SignalMonitor.objects.filter(
            user=request.user,
        ).order_by("-created_at")

        serializer = SignalMonitorListSerializer(monitors, many=True)
        return Response(
            {
                "success": True,
                "data": serializer.data,
            }
        )

    def post(self, request):
        """
        创建信号监控

        支持两种场景：
        1. 用户直接添加技术信号
        2. 从回测结果导入信号（传入 backtest_result_id）
        """
        serializer = SignalMonitorCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        monitor = SignalMonitor(
            user=request.user,
            name=data["name"],
            symbol=data["symbol"],
            interval=data.get("interval", "1h"),
            source=data.get("source", "binance"),
            indicator_type=data["indicator_type"],
            indicator_params=data.get("indicator_params", {}),
            condition=data["condition"],
            trigger_type=data.get("trigger_type", "once"),
            action_type=data.get("action_type", "notify"),
            action_params=data.get("action_params", {}),
        )

        if data.get("backtest_result_id"):
            from apps.backtest.models import BacktestResult

            try:
                backtest = BacktestResult.objects.get(id=data["backtest_result_id"])
                monitor.backtest_result = backtest
            except BacktestResult.DoesNotExist:
                pass

        if data.get("expires_at"):
            monitor.expires_at = data["expires_at"]

        monitor.save()

        return Response(
            {
                "success": True,
                "data": SignalMonitorListSerializer(monitor).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SignalMonitorDetailView(APIView):
    """单个信号监控操作"""

    permission_classes = [IsAuthenticated]

    def get(self, request, monitor_id):
        """获取信号监控详情"""
        monitor = get_object_or_404(
            SignalMonitor,
            id=monitor_id,
            user=request.user,
        )
        serializer = SignalMonitorListSerializer(monitor)
        return Response(
            {
                "success": True,
                "data": serializer.data,
            }
        )

    def patch(self, request, monitor_id):
        """更新信号监控"""
        monitor = get_object_or_404(
            SignalMonitor,
            id=monitor_id,
            user=request.user,
        )

        serializer = SignalMonitorUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        for field, value in serializer.validated_data.items():
            setattr(monitor, field, value)

        monitor.save()

        return Response(
            {
                "success": True,
                "data": SignalMonitorListSerializer(monitor).data,
            }
        )

    def delete(self, request, monitor_id):
        """删除信号监控"""
        monitor = get_object_or_404(
            SignalMonitor,
            id=monitor_id,
            user=request.user,
        )
        monitor.delete()
        return Response(
            {
                "success": True,
                "message": "已删除",
            },
            status=status.HTTP_204_NO_CONTENT,
        )


class SignalMonitorTriggerLogView(APIView):
    """信号触发日志"""

    permission_classes = [IsAuthenticated]

    def get(self, request, monitor_id):
        """获取信号触发日志"""
        monitor = get_object_or_404(
            SignalMonitor,
            id=monitor_id,
            user=request.user,
        )
        logs = SignalTriggerLog.objects.filter(
            monitor=monitor,
        ).order_by("-executed_at")[:50]

        serializer = SignalTriggerLogSerializer(logs, many=True)
        return Response(
            {
                "success": True,
                "data": serializer.data,
            }
        )


class BacktestImportView(APIView):
    """从回测结果导入信号到监控列表"""

    permission_classes = [IsAuthenticated]

    def post(self, request, backtest_id):
        """
        将回测使用的技术信号批量导入监控列表。
        """
        from apps.backtest.models import BacktestResult

        backtest = get_object_or_404(
            BacktestResult,
            id=backtest_id,
        )

        signals = request.data.get("signals", [])
        if not signals:
            return Response(
                {
                    "success": False,
                    "error": "signals 字段不能为空",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        created_monitors = []
        for signal in signals:
            monitor = SignalMonitor(
                user=request.user,
                name=signal.get(
                    "name",
                    f"{backtest.strategy.name}-{signal.get('indicator_type', 'signal')}",
                ),
                symbol=signal.get("symbol", backtest.symbol),
                interval=signal.get("interval", backtest.timeframe),
                source=signal.get("source", "binance"),
                indicator_type=signal["indicator_type"],
                indicator_params=signal.get("indicator_params", {}),
                condition=signal["condition"],
                trigger_type=signal.get("trigger_type", "once"),
                action_type=signal.get("action_type", "notify"),
                action_params=signal.get("action_params", {}),
                backtest_result=backtest,
            )
            monitor.save()
            created_monitors.append(monitor)

        serializer = SignalMonitorListSerializer(created_monitors, many=True)
        return Response(
            {
                "success": True,
                "data": serializer.data,
                "count": len(created_monitors),
            },
            status=status.HTTP_201_CREATED,
        )
