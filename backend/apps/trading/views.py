from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Order, Strategy
from .serializers import OrderSerializer, StrategySerializer


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


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trading_summary(request):
    """
    汇总交易概览：总权益、今日盈亏、活跃订单数、持仓数。
    """
    from django.utils import timezone
    from apps.exchange.models import ExchangeAccount
    from apps.trading.models import DailySnapshot

    # 从最新快照获取总权益
    latest_snapshot = DailySnapshot.objects.filter(user=request.user).order_by('-date').first()
    total_equity = str(latest_snapshot.total_equity) if latest_snapshot else '0'

    # 今日已实现盈亏
    today = timezone.now().date()
    today_filled = list(Order.objects.filter(
        created_at__date=today,
        status='filled',
        realized_pnl__isnull=False,
    ))
    today_pnl = sum(float(o.realized_pnl or 0) for o in today_filled)
    today_count = len(today_filled)

    # 活跃订单数
    active_count = Order.objects.filter(
        status__in=['pending', 'submitted', 'partial']
    ).count()

    # 交易所账户数
    account_count = ExchangeAccount.objects.filter(is_active=True).count()

    return Response({
        'total_equity': total_equity,
        'today_realized_pnl': f'{today_pnl:.2f}',
        'active_orders_count': active_count,
        'total_orders_today': today_count,
        'account_count': account_count,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def position_list(request):
    """
    获取当前持仓列表。
    优先从 OrderExecutor 实时查询，回退到空列表。
    """
    from apps.exchange.models import ExchangeAccount
    from apps.trading.executor import OrderExecutor

    executor = OrderExecutor.get_instance()
    if not executor or not executor._running:
        return Response({
            'positions': [],
            'executor_running': False,
            'message': '交易框架未启动，持仓数据暂不可用',
        })

    # 构建 exchange -> account_id 映射
    exchange_to_account = {}
    for acc in ExchangeAccount.objects.filter(is_active=True):
        exchange_to_account[acc.exchange.lower()] = str(acc.id)

    all_positions = []
    for exchange_name, adapter in executor._adapters.items():
        account_id = exchange_to_account.get(exchange_name.lower())
        try:
            positions = adapter.get_positions()
            for pos in positions:
                all_positions.append({
                    'exchange': exchange_name,
                    'exchange_account_id': account_id,
                    'symbol': pos.get('symbol', ''),
                    'side': pos.get('side', 'long'),
                    'quantity': str(pos.get('quantity', 0)),
                    'entry_price': str(pos.get('entry_price', 0)),
                    'mark_price': str(pos.get('mark_price', 0)),
                    'unrealized_pnl': str(pos.get('unrealized_pnl', 0)),
                })
        except Exception:
            continue

    return Response({
        'positions': all_positions,
        'executor_running': True,
    })


@api_view(['GET'])
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
                balance = adapter.get_balance()
                balances[exchange_name] = balance
            except Exception:
                pass

    # 合并余额信息
    for acc in account_data:
        exchange = acc['exchange'].lower()
        acc['balance'] = balances.get(exchange)

    return Response(account_data)
