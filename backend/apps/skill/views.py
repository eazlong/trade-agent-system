from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .loader import get_skills_loader


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_skills(request):
    """列出所有可用的 Skills（全局 + agent 独有）"""
    agent_name = request.query_params.get('agent', '')
    loader = get_skills_loader(agent_name)
    skills = loader.list_skills()
    return Response({'skills': skills})
