from __future__ import annotations

from asgiref.sync import async_to_sync
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.agent.base import AgentMessage, AgentResult
from apps.agent.supervisor import SupervisorAgent
from apps.agent.frame_manager import FrameManager


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def chat(request: Request) -> Response:
    """REST入口：用户发送消息给SupervisorAgent"""
    text = request.data.get("text", "").strip()
    if not text:
        return Response({"error": "text is required"}, status=400)

    supervisor = SupervisorAgent.get_instance()
    result: AgentResult = async_to_sync(supervisor.handle)(
        AgentMessage(
            sender="user",
            recipient="supervisor",
            payload={"text": text},
            user_id=str(request.user.pk),
        )
    )

    if result.success:
        return Response({"data": result.data, "task_id": result.task_id})
    return Response({"error": result.error, "task_id": result.task_id}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def frame_status(request: Request) -> Response:
    """查询框架运行状态"""
    fm = FrameManager.get_instance()
    return Response(fm.status())


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def frame_control(request: Request) -> Response:
    """启动/停止框架: {"frame": "trading|assist", "action": "start|stop"}"""
    frame = request.data.get("frame")
    action = request.data.get("action")
    if frame not in ("trading", "assist") or action not in ("start", "stop"):
        return Response({"error": "Invalid frame or action"}, status=400)

    fm = FrameManager.get_instance()
    try:
        if action == "start":
            async_to_sync(fm.start)(frame)
        else:
            async_to_sync(fm.stop)(frame)
        return Response({"frame": frame, "action": action, "status": "ok"})
    except Exception as e:
        return Response({"error": str(e)}, status=500)
