from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.agent.models import AgentMemory


def _get_user_id(request: Request) -> str | None:
    """Get user_id from request (authenticated user or query param)."""
    if hasattr(request, "user") and request.user and request.user.is_authenticated:
        return str(request.user.id)
    user_id = request.query_params.get("user_id")
    if not user_id:
        return None
    return user_id


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_memories(request: Request) -> Response:
    user_id = _get_user_id(request)
    if not user_id:
        return Response(
            {"error": "user_id required"}, status=status.HTTP_400_BAD_REQUEST
        )

    agent_type = request.query_params.get("agent_type")
    qs = AgentMemory.objects.filter(user_id=user_id).order_by("-created_at")[:50]
    if agent_type:
        qs = AgentMemory.objects.filter(
            user_id=user_id, agent_type=agent_type
        ).order_by("-created_at")[:50]
    data = list(qs.values("id", "agent_type", "content", "metadata", "created_at"))
    return Response(data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_memory(request: Request, pk: str) -> Response:
    user_id = _get_user_id(request)
    if not user_id:
        return Response(
            {"error": "user_id required"}, status=status.HTTP_400_BAD_REQUEST
        )

    try:
        mem = get_object_or_404(AgentMemory, pk=pk, user_id=user_id)
        mem.delete()
        return Response({"deleted": str(pk)})
    except AgentMemory.DoesNotExist:
        return Response({"error": "not found"}, status=404)
