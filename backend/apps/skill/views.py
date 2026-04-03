from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from apps.skill.registry import SkillRegistry


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_skills(request):
    """列出已注册的所有Skill"""
    return Response({'skills': SkillRegistry.all_names()})
