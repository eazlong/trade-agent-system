import logging
from decimal import Decimal

from asgiref.sync import async_to_sync
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from .models import Order, Strategy, LiveSession
from .serializers import OrderSerializer, StrategySerializer, LiveSessionSerializer

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_list(request):
    orders = Order.objects.select_related("exchange_account").order_by("-created_at")[
        :100
    ]
    return Response(OrderSerializer(orders, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_detail(request, pk):
    order = Order.objects.get(pk=pk)
    return Response(OrderSerializer(order).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def strategy_list(request):
    if request.method == "GET":
        strategies = Strategy.objects.all().order_by("-created_at")
        return Response(StrategySerializer(strategies, many=True).data)
    serializer = StrategySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def trading_summary(request):
    from django.utils import timezone
    from apps.exchange.models import ExchangeAccount
    from apps.trading.models import DailySnapshot

    # 从最新快照获取总权益
    latest_snapshot = (
        DailySnapshot.objects.filter(user=request.user).order_by("-date").first()
    )
    total_equity = str(latest_snapshot.total_equity) if latest_snapshot else "0"

    # 今日已实现盈亏
    today = timezone.now().date()
    today_filled = list(
        Order.objects.filter(
            created_at__date=today,
            status="filled",
            realized_pnl__isnull=False,
        )
    )
    today_pnl = sum(float(o.realized_pnl or 0) for o in today_filled)
    today_count = len(today_filled)

    # 活跃订单数
    active_count = Order.objects.filter(
        status__in=["pending", "submitted", "partial"]
    ).count()

    # 交易所账户数
    account_count = ExchangeAccount.objects.filter(is_active=True).count()

    return Response(
        {
            "total_equity": total_equity,
            "today_realized_pnl": f"{today_pnl:.2f}",
            "active_orders_count": active_count,
            "total_orders_today": today_count,
            "account_count": account_count,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def position_list(request):
    from apps.exchange.models import ExchangeAccount
    from apps.trading.executor import OrderExecutor

    executor = OrderExecutor.get_instance()
    if not executor or not executor._running:
        return Response(
            {
                "positions": [],
                "executor_running": False,
                "message": "交易框架未启动，持仓数据暂不可用",
            }
        )

    # 构建 exchange -> account_id 映射
    exchange_to_account = {}
    for acc in ExchangeAccount.objects.filter(is_active=True):
        exchange_to_account[acc.exchange.lower()] = str(acc.id)

    all_positions = []
    for exchange_name, adapter in executor._adapters.items():
        account_id = exchange_to_account.get(exchange_name.lower())
        try:
            positions = async_to_sync(adapter.get_positions)()
            for pos in positions:
                # pos is a Position dataclass
                all_positions.append(
                    {
                        "exchange": exchange_name,
                        "exchange_account_id": account_id,
                        "symbol": pos.symbol,
                        "side": pos.side,
                        "quantity": str(pos.quantity),
                        "entry_price": str(pos.entry_price),
                        "mark_price": str(
                            pos.entry_price
                        ),  # Position has no mark_price yet
                        "unrealized_pnl": str(pos.unrealized_pnl),
                    }
                )
        except Exception:
            continue

    return Response(
        {
            "positions": all_positions,
            "executor_running": True,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def account_list(request):
    """
    返回交易所账户列表 + 各自的余额信息。
    """
    from apps.exchange.models import ExchangeAccount
    from apps.exchange.serializers import ExchangeAccountSerializer
    from apps.trading.executor import OrderExecutor

    accounts = ExchangeAccount.objects.filter(is_active=True)
    serializer = ExchangeAccountSerializer(accounts, many=True)
    account_data = serializer.data

    # 尝试从 OrderExecutor 获取各账户余额
    executor = OrderExecutor.get_instance()
    balances = {}
    if executor and executor._running:
        for exchange_name, adapter in executor._adapters.items():
            try:
                balance = async_to_sync(adapter.get_balance)()
                # balance is dict[str, Decimal], e.g. {"USDT": Decimal("100.0")}
                usdt_balance = balance.get("USDT", Decimal("0"))
                balances[exchange_name] = {
                    "total": str(usdt_balance),
                    "available": str(usdt_balance),
                    "used": "0",
                }
            except Exception:
                pass

    # 合并余额信息
    for acc in account_data:
        exchange = acc["exchange"].lower()
        acc["balance"] = balances.get(exchange)

    return Response(account_data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def live_session_list(request):
    sessions = LiveSession.objects.filter(user=request.user).order_by("-created_at")[
        :50
    ]
    return Response(LiveSessionSerializer(sessions, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def live_session_detail(request, pk):
    try:
        session = LiveSession.objects.get(pk=pk, user=request.user)
    except LiveSession.DoesNotExist:
        return Response({"error": "Live session not found"}, status=404)
    return Response(LiveSessionSerializer(session).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def live_session_create(request):
    """
    从回测创建并启动实盘会话。

    Request body:
      backtest_result_id: str (UUID) — 来源回测
      mode: str — 'paper' 或 'live'（首次必须为 paper）
      exchange_account_id: str (UUID) — 交易所账户
      config: dict (optional) — 运行参数
    """
    from apps.backtest.models import BacktestResult
    from apps.exchange.models import ExchangeAccount

    backtest_id = request.data.get("backtest_result_id")
    mode = request.data.get("mode", "paper")
    exchange_account_id = request.data.get("exchange_account_id")
    config = request.data.get("config", {})

    if not backtest_id:
        return Response({"error": "backtest_result_id is required"}, status=400)
    if not exchange_account_id:
        return Response({"error": "exchange_account_id is required"}, status=400)

    # 校验回测结果
    try:
        backtest = BacktestResult.objects.get(pk=backtest_id)
    except BacktestResult.DoesNotExist:
        return Response({"error": "Backtest result not found"}, status=404)

    if backtest.review_status != "approved":
        return Response(
            {"error": "Backtest must be approved before deployment"}, status=400
        )

    # 强制先模拟：live 模式必须有 paper 会话记录
    if mode == "live":
        has_paper = LiveSession.objects.filter(
            user=request.user,
            backtest_result=backtest,
            mode="paper",
        ).exists()
        if not has_paper:
            return Response(
                {"error": "Must run a paper trading session first before going live"},
                status=400,
            )

    # 校验交易所账户
    try:
        exchange_account = ExchangeAccount.objects.get(
            pk=exchange_account_id, is_active=True
        )
    except ExchangeAccount.DoesNotExist:
        return Response({"error": "Exchange account not found or inactive"}, status=404)

    # 创建 LiveSession
    session = LiveSession.objects.create(
        user=request.user,
        backtest_result=backtest,
        strategy=backtest.strategy,
        symbol=backtest.symbol,
        mode=mode,
        status="pending",
        exchange_account=exchange_account,
        initial_capital=backtest.initial_capital,
        current_equity=backtest.initial_capital,
        config=config,
    )

    return Response(
        {
            "id": str(session.id),
            "status": session.status,
            "mode": session.mode,
            "message": f"Live session created ({mode} mode)",
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def live_session_start(request, pk):
    from apps.agent.frame_manager import FrameManager

    try:
        session = LiveSession.objects.select_related(
            "strategy", "exchange_account", "user", "backtest_result"
        ).get(pk=pk, user=request.user)
    except LiveSession.DoesNotExist:
        return Response({"error": "Live session not found"}, status=404)

    if session.status not in ("pending", "error", "running", "stopped"):
        return Response(
            {"error": f"Session is {session.status}, cannot start"}, status=400
        )

    if not session.exchange_account:
        return Response({"error": "No exchange account configured"}, status=400)

    frame_manager = FrameManager.get_instance()

    # 如果已在运行，先停掉旧框架（处理之前框架崩溃导致的幽灵状态）
    if session.status == "running":
        logger.warning(
            "Session %s is already running, stopping old frame before restart", pk
        )
        try:
            async_to_sync(frame_manager.stop_strategy_runner)()
        except Exception:
            pass
        try:
            async_to_sync(frame_manager.stop_trading_frame)()
        except Exception:
            pass

    # 1. 启动交易框架
    try:
        async_to_sync(frame_manager.start_trading_frame)(mode=session.mode)
    except Exception as e:
        logger.exception("Failed to start trading frame for session %s: %s", pk, e)
        return Response({"error": f"Failed to start trading frame: {e}"}, status=500)

    # 2. 启动策略运行器（内部会注册信号监控到 SignalMonitor）
    timeframe = session.backtest_result.timeframe if session.backtest_result else "1h"

    try:
        async_to_sync(frame_manager.start_strategy_runner)(
            strategy_name=session.strategy.name,
            symbol=session.symbol,
            timeframe=timeframe,
            parameters=session.config or {},
            exchange_account_id=str(session.exchange_account.id),
            user_id=str(session.user.id),
            live_session_id=str(session.id),
            initial_balance=session.initial_capital,
        )
    except Exception as e:
        logger.exception("Failed to start strategy for session %s: %s", pk, e)
        # 回滚已启动的交易框架
        async_to_sync(frame_manager.stop_trading_frame)()
        return Response({"error": f"Failed to start strategy: {e}"}, status=500)

    session.status = "running"
    session.started_at = timezone.now()
    session.stopped_at = None
    session.save(update_fields=["status", "started_at", "stopped_at", "updated_at"])

    logger.info(
        "Live session %s started: strategy=%s symbol=%s timeframe=%s",
        pk,
        session.strategy.name,
        session.symbol,
        timeframe,
    )

    return Response({"status": session.status, "message": "Trading framework started"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def live_session_pause(request, pk):
    try:
        session = LiveSession.objects.get(pk=pk, user=request.user)
    except LiveSession.DoesNotExist:
        return Response({"error": "Live session not found"}, status=404)

    if session.status != "running":
        return Response(
            {"error": f"Session is {session.status}, cannot pause"}, status=400
        )

    session.status = "paused"
    session.save(update_fields=["status", "updated_at"])

    return Response({"status": session.status, "message": "Session paused"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def live_session_resume(request, pk):
    try:
        session = LiveSession.objects.get(pk=pk, user=request.user)
    except LiveSession.DoesNotExist:
        return Response({"error": "Live session not found"}, status=404)

    if session.status != "paused":
        return Response(
            {"error": f"Session is {session.status}, cannot resume"}, status=400
        )

    session.status = "running"
    session.save(update_fields=["status", "updated_at"])

    return Response({"status": session.status, "message": "Session resumed"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def live_session_stop(request, pk):
    from apps.agent.frame_manager import FrameManager

    try:
        session = LiveSession.objects.get(pk=pk, user=request.user)
    except LiveSession.DoesNotExist:
        return Response({"error": "Live session not found"}, status=404)

    if session.status not in ("running", "paused"):
        return Response(
            {"error": f"Session is {session.status}, cannot stop"}, status=400
        )

    session.status = "stopped"
    session.stopped_at = timezone.now()
    session.save(update_fields=["status", "stopped_at", "updated_at"])

    # 停止策略运行器和交易框架
    frame_manager = FrameManager.get_instance()
    async_to_sync(frame_manager.stop_strategy_runner)()
    async_to_sync(frame_manager.stop_trading_frame)()

    return Response({"status": session.status, "message": "Session stopped"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def live_session_promote(request, pk):
    try:
        session = LiveSession.objects.get(pk=pk, user=request.user)
    except LiveSession.DoesNotExist:
        return Response({"error": "Live session not found"}, status=404)

    if session.mode != "paper":
        return Response({"error": "Only paper sessions can be promoted"}, status=400)

    if session.status not in ("running", "stopped", "paused"):
        return Response(
            {"error": f"Session is {session.status}, cannot promote"}, status=400
        )

    # 停止当前 paper 会话
    session.status = "stopped"
    session.stopped_at = timezone.now()
    session.save(update_fields=["status", "stopped_at", "updated_at"])

    # 创建新的 live 会话
    live_session = LiveSession.objects.create(
        user=request.user,
        backtest_result=session.backtest_result,
        strategy=session.strategy,
        symbol=session.symbol,
        mode="live",
        status="pending",
        exchange_account=session.exchange_account,
        initial_capital=session.current_equity or session.initial_capital,
        current_equity=session.current_equity or session.initial_capital,
        config=session.config,
    )

    return Response(
        {
            "id": str(live_session.id),
            "mode": live_session.mode,
            "status": live_session.status,
            "message": "Promoted to live trading session",
        },
        status=status.HTTP_201_CREATED,
    )
