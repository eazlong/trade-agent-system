from datetime import date, timedelta

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
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
    page = int(request.query_params.get("page", 1))
    page_size = min(int(request.query_params.get("page_size", 20)), 200)
    queryset = BacktestResult.objects.order_by("-created_at")
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page)
    return Response(
        {
            "count": paginator.count,
            "num_pages": paginator.num_pages,
            "current_page": page_obj.number,
            "results": BacktestResultSerializer(page_obj, many=True).data,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def result_detail(request, pk):
    result = BacktestResult.objects.get(pk=pk)
    return Response(BacktestResultSerializer(result).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def result_detail_full(request, pk):
    """Returns summary + equity_curve + drawdown_curve (no trades)."""
    result = BacktestResult.objects.get(pk=pk)
    return Response(BacktestDetailSerializer(result).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def result_trades(request, pk):
    """Paginated trade log for a backtest result.

    Query params:
      page: int (default 1)
      page_size: int (default 50, max 200)
      sort: 'entry_time' | '-entry_time' | 'pnl' | '-pnl' (default 'entry_time')
    """
    if not BacktestResult.objects.filter(pk=pk).exists():
        return Response({"error": "Backtest result not found"}, status=404)

    sort = request.query_params.get("sort", "entry_time")
    page_size = min(int(request.query_params.get("page_size", 50)), 200)

    trades = BacktestTrade.objects.filter(backtest_id=pk).order_by(sort)
    paginator = Paginator(trades, page_size)
    page = paginator.get_page(request.query_params.get("page", 1))

    return Response(
        {
            "count": paginator.count,
            "num_pages": paginator.num_pages,
            "current_page": page.number,
            "results": BacktestTradeSerializer(page, many=True).data,
        }
    )


@api_view(["POST"])
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
        return Response({"error": "Backtest result not found"}, status=404)

    if result.review_status != "pending":
        return Response(
            {"error": f"Result already {result.review_status}"},
            status=400,
        )

    approved = request.data.get("approved")
    notes = request.data.get("notes", "")

    if approved is None:
        return Response({"error": "approved field is required"}, status=400)

    result.review_status = "approved" if approved else "rejected"
    result.review_notes = notes
    result.reviewed_at = timezone.now()
    result.save(update_fields=["review_status", "review_notes", "reviewed_at"])

    return Response(
        {
            "id": str(result.id),
            "review_status": result.review_status,
            "review_notes": result.review_notes,
            "reviewed_at": result.reviewed_at,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def result_create(request):
    """创建并提交回测任务。

    立即创建占位 BacktestResult 记录（UI 即时可见），
    然后异步提交 Celery 任务运行回测。

    Request body (CreateBacktestPayload):
      strategy_name: str — 策略名称
      symbol: str — 交易对
      timeframe: str — K 线周期
      start_date: str (optional) — 回测开始日期
      end_date: str (optional) — 回测结束日期
      initial_capital: float (optional) — 初始资金
      commission_rate: float (optional) — 手续费率
      parameters: dict (optional) — 策略参数
      exchange: str (optional) — 交易所
    """
    strategy_name = request.data.get("strategy_name", "")
    symbol = request.data.get("symbol", "")
    timeframe = request.data.get("timeframe", "")
    if not strategy_name or not symbol or not timeframe:
        return Response(
            {"error": "strategy_name, symbol, timeframe 为必填项"}, status=400
        )

    from apps.strategy_engine.backtest_mode import (
        create_empty_result,
        _resolve_strategy_id,
    )
    from apps.trading.models import Strategy
    import asyncio

    async def _resolve():
        return await _resolve_strategy_id(strategy_name)

    try:
        strategy_id = asyncio.run(_resolve())
        if not strategy_id:
            return Response({"error": f"无法解析策略: {strategy_name}"}, status=400)
        # 使用数据库中的标准策略名，而非用户提交的（可能不一致的）名称
        canonical_name = Strategy.objects.get(id=strategy_id).name
    except Exception as e:
        return Response({"error": f"策略解析失败: {e}"}, status=400)

    start_date = request.data.get("start_date", "")
    if not start_date:
        start_date = (date.today() - timedelta(days=30)).isoformat()
    end_date = request.data.get("end_date", "")
    if not end_date:
        end_date = date.today().isoformat()

    initial_capital = request.data.get("initial_capital", 10000)
    commission_rate = request.data.get("commission_rate", 0.001)
    parameters = request.data.get("parameters")
    exchange = request.data.get("exchange", "binance")
    user_id = str(request.user.id) if request.user.is_authenticated else None

    result_id = create_empty_result(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        parameters=parameters or {},
        user_id=user_id,
    )
    if not result_id:
        return Response({"error": "创建回测记录失败"}, status=500)

    from .tasks import run_backtest_task

    task = run_backtest_task.apply_async(
        kwargs={
            "strategy_name": canonical_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "initial_capital": initial_capital,
            "commission_rate": commission_rate,
            "parameters": parameters,
            "strategy_id": strategy_id,
            "exchange": exchange,
            "start_date": start_date,
            "end_date": end_date,
            "user_id": user_id,
            "result_id": result_id,
        }
    )

    return Response(
        {
            "task_id": task.id,
            "result_id": result_id,
            "message": f"回测任务已提交，task_id={task.id}",
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def result_rerun(request, pk):
    """重新运行回测任务。

    基于已有回测结果的配置重新提交 Celery 任务，
    创建新的占位记录并异步运行回测。
    """
    try:
        source = BacktestResult.objects.select_related("strategy").get(pk=pk)
    except BacktestResult.DoesNotExist:
        return Response({"error": "Backtest result not found"}, status=404)

    strategy_name = source.strategy.name if source.strategy else ""
    symbol = source.symbol
    timeframe = source.timeframe
    start_date = source.start_date.isoformat()
    end_date = source.end_date.isoformat()
    initial_capital = float(source.initial_capital)
    parameters = source.parameters or {}
    user_id = str(request.user.id) if request.user.is_authenticated else None

    from apps.strategy_engine.backtest_mode import create_empty_result

    result_id = create_empty_result(
        strategy_id=str(source.strategy_id),
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        parameters=parameters,
        user_id=user_id,
    )
    if not result_id:
        return Response({"error": "创建回测记录失败"}, status=500)

    from .tasks import run_backtest_task

    task = run_backtest_task.apply_async(
        kwargs={
            "strategy_name": strategy_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "initial_capital": initial_capital,
            "commission_rate": 0.001,
            "parameters": parameters,
            "strategy_id": str(source.strategy_id),
            "exchange": "binance",
            "start_date": start_date,
            "end_date": end_date,
            "user_id": user_id,
            "result_id": result_id,
        }
    )

    return Response(
        {
            "task_id": task.id,
            "result_id": result_id,
            "message": f"回测任务已重新提交，task_id={task.id}",
        }
    )
