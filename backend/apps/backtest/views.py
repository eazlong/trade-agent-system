from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import BacktestResult
from .serializers import BacktestResultSerializer


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def result_list(request):
    results = BacktestResult.objects.order_by('-created_at')[:50]
    return Response(BacktestResultSerializer(results, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def result_detail(request, pk):
    result = BacktestResult.objects.get(pk=pk)
    return Response(BacktestResultSerializer(result).data)
