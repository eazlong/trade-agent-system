from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Order, Strategy
from .serializers import OrderSerializer, StrategySerializer


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def order_list(request):
    orders = Order.objects.select_related('exchange_account').order_by('-created_at')[:100]
    return Response(OrderSerializer(orders, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def order_detail(request, pk):
    order = Order.objects.get(pk=pk)
    return Response(OrderSerializer(order).data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def strategy_list(request):
    if request.method == 'GET':
        strategies = Strategy.objects.all().order_by('-created_at')
        return Response(StrategySerializer(strategies, many=True).data)
    serializer = StrategySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)
