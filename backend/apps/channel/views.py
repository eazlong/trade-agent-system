from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def status(request):
    """Return channel/framework status"""
    from apps.agent.frame_manager import FrameManager
    fm = FrameManager.get_instance()
    return Response(fm.status())
