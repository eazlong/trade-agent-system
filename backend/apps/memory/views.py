from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.agent.models import AgentMemory


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_memories(request: Request) -> Response:
    agent_type = request.query_params.get("agent_type")
    qs = AgentMemory.objects.all().order_by("-created_at")[:50]
    if agent_type:
        qs = AgentMemory.objects.filter(agent_type=agent_type).order_by("-created_at")[
            :50
        ]
    data = list(qs.values("id", "agent_type", "content", "metadata", "created_at"))
    return Response(data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_memory(request: Request, pk: str) -> Response:
    try:
        mem = AgentMemory.objects.get(pk=pk)
        mem.delete()
        return Response({"deleted": str(pk)})
    except AgentMemory.DoesNotExist:
        return Response({"error": "not found"}, status=404)
