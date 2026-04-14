from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.core.paginator import Paginator
from django.utils import timezone
from .models import BacktestResult, BacktestTrade
from .serializers import (
    BacktestResultSerializer,
    BacktestDetailSerializer,
    BacktestTradeSerializer,
)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def result_list(request):
    results = BacktestResult.objects.order_by("-created_at")[:50]
    return Response(BacktestResultSerializer(results, many=True).data)


@api_view(["GET"])
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


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def result_review(request, pk):
    """审核回测结果

    Request body:
      approved: bool  — 是否通过审核
      notes: str (optional) — 审核备注
    """
    try:
        result = BacktestResult.objects.get(pk=pk)
    except BacktestResult.DoesNotExist:
        return Response({'error': 'Backtest result not found'}, status=404)

    if result.review_status != 'pending':
        return Response(
            {'error': f'Result already {result.review_status}'},
            status=400,
        )

    approved = request.data.get('approved')
    notes = request.data.get('notes', '')

    if approved is None:
        return Response({'error': 'approved field is required'}, status=400)

    result.review_status = 'approved' if approved else 'rejected'
    result.review_notes = notes
    result.reviewed_at = timezone.now()
    result.save(update_fields=['review_status', 'review_notes', 'reviewed_at'])

    return Response({
        'id': str(result.id),
        'review_status': result.review_status,
        'review_notes': result.review_notes,
        'reviewed_at': result.reviewed_at,
    })
