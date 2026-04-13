from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import RiskEvent, RiskConfig
from .serializers import RiskEventSerializer, RiskConfigSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def event_list(request):
    events = RiskEvent.objects.order_by("-created_at")[:50]
    return Response(RiskEventSerializer(events, many=True).data)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def config(request):
    cfg, _ = RiskConfig.objects.get_or_create(pk=1)
    if request.method == "GET":
        return Response(RiskConfigSerializer(cfg).data)
    serializer = RiskConfigSerializer(cfg, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)
