from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.core.paginator import Paginator
from .models import BacktestResult, BacktestTrade
from .serializers import (
    BacktestResultSerializer,
    BacktestDetailSerializer,
    BacktestTradeSerializer,
)


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


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def result_detail_full(request, pk):
    """Returns summary + equity_curve + drawdown_curve (no trades)."""
    result = BacktestResult.objects.get(pk=pk)
    return Response(BacktestDetailSerializer(result).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def result_trades(request, pk):
    """Paginated trade log for a backtest result.

    Query params:
      page: int (default 1)
      page_size: int (default 50, max 200)
      sort: 'entry_time' | '-entry_time' | 'pnl' | '-pnl' (default 'entry_time')
    """
    if not BacktestResult.objects.filter(pk=pk).exists():
        return Response({'error': 'Backtest result not found'}, status=404)

    sort = request.query_params.get('sort', 'entry_time')
    page_size = min(int(request.query_params.get('page_size', 50)), 200)

    trades = BacktestTrade.objects.filter(backtest_id=pk).order_by(sort)
    paginator = Paginator(trades, page_size)
    page = paginator.get_page(request.query_params.get('page', 1))

    return Response({
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page.number,
        'results': BacktestTradeSerializer(page, many=True).data,
    })
