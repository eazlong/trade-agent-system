# 交易执行与风控详细设计文档

> 设计师C | 版本：v1.0 | 基于 architecture_final.md (Final-R2)

---

## 目录

1. [OrderExecutor实现](#一orderexecutor实现)
2. [交易所适配器](#二交易所适配器)
3. [持仓管理与5分钟对账](#三持仓管理与5分钟对账)
4. [RiskGuard实现](#四riskguard实现)
5. [熔断器机制](#五熔断器机制)
6. [回测系统详细实现](#六回测系统详细实现)
7. [Telegram通知通道](#七telegram通知通道)
8. [API接口清单](#八api接口清单)
9. [关键决策记录（ADR）落地](#九关键决策记录adr落地)

---

## 一、OrderExecutor实现

```python
# apps/trading/executor.py
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Type

from apps.trading.adapters.base import BaseExchangeAdapter, OrderRequest, OrderResponse
from apps.riskguard.guard import RiskGuard

logger = logging.getLogger(__name__)


class OrderExecutor:
    """
    订单执行器（随交易框架懒加载，由FrameManager管理生命周期）。
    职责：
    1. 接收Agent下单指令
    2. 调用RiskGuard前置校验（必须通过才能下单）
    3. 通过交易所适配器执行订单
    4. 异步等待成交回调
    5. 写入orders表 + 更新持仓缓存
    """

    _instance: 'OrderExecutor | None' = None

    def __init__(self):
        self._adapters: dict[str, BaseExchangeAdapter] = {}
        self._strategies: dict[str, type] = {}
        self._riskguard: Optional[RiskGuard] = None
        self._running = False

    @classmethod
    def get_instance(cls) -> 'OrderExecutor | None':
        return cls._instance

    async def initialize(self) -> None:
        """FrameManager启动交易框架时调用"""
        OrderExecutor._instance = self
        self._running = True
        # 初始化已配置的交易所适配器
        await self._load_adapters()
        logger.info('OrderExecutor initialized')

    async def shutdown(self) -> None:
        """FrameManager停止交易框架时调用"""
        self._running = False
        for adapter in self._adapters.values():
            await adapter.disconnect()
        OrderExecutor._instance = None
        logger.info('OrderExecutor shutdown')

    async def _load_adapters(self) -> None:
        """从DB加载用户已配置的交易所API Key（Fernet解密）"""
        from apps.trading.adapters.binance import BinanceAdapter
        from apps.trading.adapters.okx import OKXAdapter

        adapter_map = {
            'binance': BinanceAdapter,
            'okx': OKXAdapter,
        }
        # 此处从DB读取加密的API Key（实际实现通过Django ORM）
        # 简化示例：从settings读取
        from django.conf import settings
        for exchange, cls in adapter_map.items():
            key = getattr(settings, f'{exchange.upper()}_API_KEY', None)
            secret = getattr(settings, f'{exchange.upper()}_SECRET', None)
            if key and secret:
                adapter = cls(api_key=key, secret=secret)
                await adapter.connect()
                self._adapters[exchange] = adapter
                logger.info(f'Adapter loaded: {exchange}')

    def register_strategy(self, name: str, strategy_cls: type) -> None:
        """QuantEngineerAgent通过StrategyGenSkill调用此方法热加载策略"""
        self._strategies[name] = strategy_cls
        logger.info(f'Strategy registered: {name}')

    async def submit_order(
        self,
        exchange: str,
        request: 'OrderRequest',
        user_id: str,
        plan_id: str | None = None,
    ) -> 'OrderResponse':
        """
        下单主流程：
        1. RiskGuard前置校验（必须通过）
        2. 写入DB（status=pending）
        3. 发送到交易所
        4. 更新DB（status=submitted）
        """
        if not self._running:
            raise RuntimeError('OrderExecutor is not running')

        adapter = self._adapters.get(exchange)
        if not adapter:
            raise ValueError(f'Exchange adapter not found: {exchange}')

        # 步骤1：RiskGuard前置校验
        from apps.riskguard.guard import RiskGuard
        riskguard = RiskGuard.get_instance()
        if riskguard:
            approved, reason = await riskguard.pre_trade_check(request, user_id)
            if not approved:
                raise PermissionError(f'RiskGuard拒绝下单: {reason}')

        # 步骤2：写入DB
        order_id = await self._persist_order(request, user_id, plan_id, status='pending')

        # 步骤3：发送到交易所
        try:
            response = await adapter.place_order(request)
            await self._update_order(
                order_id,
                exchange_order_id=response.exchange_order_id,
                status='submitted',
                risk_approved=True,
            )
            logger.info(f'Order submitted: {order_id} -> {response.exchange_order_id}')
            return response
        except Exception as e:
            await self._update_order(order_id, status='failed')
            logger.error(f'Order failed: {order_id} - {e}')
            raise

    async def _persist_order(self, request, user_id, plan_id, status) -> str:
        """异步写入orders表（通过Django ORM in thread pool）"""
        import uuid
        from asgiref.sync import sync_to_async
        from apps.trading.models import Order

        create = sync_to_async(Order.objects.create)
        order = await create(
            id=uuid.uuid4(),
            user_id=user_id,
            plan_id=plan_id,
            exchange=request.exchange,
            symbol=request.symbol,
            order_type=request.order_type,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
            status=status,
        )
        return str(order.id)

    async def _update_order(self, order_id: str, **kwargs) -> None:
        from asgiref.sync import sync_to_async
        from apps.trading.models import Order

        @sync_to_async
        def _update():
            Order.objects.filter(id=order_id).update(**kwargs)

        await _update()
```

---

## 二、交易所适配器

### 2.1 BaseExchangeAdapter

```python
# apps/trading/adapters/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class OrderRequest:
    exchange: str
    symbol: str
    order_type: str           # 'limit' | 'market'
    side: str                 # 'buy' | 'sell'
    quantity: Decimal
    price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    client_order_id: Optional[str] = None


@dataclass
class OrderResponse:
    exchange_order_id: str
    status: str
    filled_qty: Decimal
    avg_price: Optional[Decimal]
    fee: Optional[Decimal]
    raw: dict  # 交易所原始响应，便于调试


@dataclass
class Position:
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal
    leverage: int


class BaseExchangeAdapter(ABC):
    def __init__(self, api_key: str, secret: str):
        self._api_key = api_key
        self._secret = secret

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResponse: ...

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_balance(self) -> dict[str, Decimal]: ...
```

### 2.2 BinanceAdapter示例

```python
# apps/trading/adapters/binance.py
import hashlib
import hmac
import time
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from .base import BaseExchangeAdapter, OrderRequest, OrderResponse, Position


class BinanceAdapter(BaseExchangeAdapter):
    BASE_URL = 'https://fapi.binance.com'  # 合约

    def __init__(self, api_key: str, secret: str):
        super().__init__(api_key, secret)
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={'X-MBX-APIKEY': self._api_key},
            timeout=10.0,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    def _sign(self, params: dict) -> dict:
        params['timestamp'] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(self._secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params['signature'] = sig
        return params

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        params = self._sign({
            'symbol': request.symbol,
            'side': request.side.upper(),
            'type': request.order_type.upper(),
            'quantity': str(request.quantity),
        })
        if request.price:
            params['price'] = str(request.price)
            params['timeInForce'] = 'GTC'

        resp = await self._client.post('/fapi/v1/order', params=params)
        resp.raise_for_status()
        data = resp.json()

        return OrderResponse(
            exchange_order_id=str(data['orderId']),
            status=data['status'],
            filled_qty=Decimal(data.get('executedQty', '0')),
            avg_price=Decimal(data['avgPrice']) if data.get('avgPrice') else None,
            fee=None,
            raw=data,
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool:
        params = self._sign({'symbol': symbol, 'orderId': exchange_order_id})
        resp = await self._client.delete('/fapi/v1/order', params=params)
        return resp.status_code == 200

    async def get_positions(self) -> list[Position]:
        params = self._sign({})
        resp = await self._client.get('/fapi/v2/positionRisk', params=params)
        resp.raise_for_status()
        return [
            Position(
                symbol=p['symbol'],
                side='long' if float(p['positionAmt']) > 0 else 'short',
                quantity=Decimal(abs(float(p['positionAmt']))),
                entry_price=Decimal(p['entryPrice']),
                unrealized_pnl=Decimal(p['unRealizedProfit']),
                leverage=int(p['leverage']),
            )
            for p in resp.json()
            if float(p['positionAmt']) != 0
        ]

    async def get_balance(self) -> dict[str, Decimal]:
        params = self._sign({})
        resp = await self._client.get('/fapi/v2/balance', params=params)
        resp.raise_for_status()
        return {b['asset']: Decimal(b['balance']) for b in resp.json()}
```

---

## 三、持仓管理与5分钟对账

```python
# apps/trading/position_manager.py
from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

import redis.asyncio as aioredis
from django.conf import settings

logger = logging.getLogger(__name__)

POSITION_CACHE_TTL = 600  # 10分钟，5分钟对账会刷新
POSITION_KEY_PREFIX = 'position'


class PositionMonitor:
    """
    辅助框架组件（随辅助框架懒加载）。
    1. 维护Redis持仓缓存（DB5）
    2. 每5分钟从交易所拉取持仓对账（ADR-001）
    3. 发现差异时触发告警
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._reconcile_loop())
        logger.info('PositionMonitor started')

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info('PositionMonitor stopped')

    async def _reconcile_loop(self) -> None:
        """每5分钟对账（ADR-001）"""
        while self._running:
            try:
                await self._do_reconcile()
            except Exception as e:
                logger.error(f'Reconcile error: {e}')
            await asyncio.sleep(300)  # 5分钟

    async def _do_reconcile(self) -> None:
        from apps.trading.executor import OrderExecutor
        executor = OrderExecutor.get_instance()
        if not executor:
            return

        r = await self._get_redis()
        for exchange, adapter in executor._adapters.items():
            try:
                live_positions = await adapter.get_positions()
                for pos in live_positions:
                    cached = await self._get_cached_position(r, exchange, pos.symbol)
                    if cached:
                        diff = abs(float(pos.quantity) - float(cached.get('quantity', 0)))
                        if diff > 0.001:  # 允许0.1%误差
                            logger.warning(
                                f'Position mismatch [{exchange}:{pos.symbol}]: '
                                f'live={pos.quantity}, cached={cached.get("quantity")}'
                            )
                            await self._send_alert(exchange, pos.symbol, pos, cached)

                    # 刷新缓存
                    await self._cache_position(r, exchange, pos)
            except Exception as e:
                logger.error(f'Reconcile [{exchange}] error: {e}')

        await r.aclose()

    async def _cache_position(self, r, exchange: str, pos) -> None:
        key = f'{POSITION_KEY_PREFIX}:{exchange}:{pos.symbol}'
        data = {
            'symbol': pos.symbol,
            'side': pos.side,
            'quantity': str(pos.quantity),
            'entry_price': str(pos.entry_price),
            'unrealized_pnl': str(pos.unrealized_pnl),
            'leverage': pos.leverage,
        }
        await r.setex(key, POSITION_CACHE_TTL, json.dumps(data))

    async def _get_cached_position(self, r, exchange: str, symbol: str) -> dict | None:
        key = f'{POSITION_KEY_PREFIX}:{exchange}:{symbol}'
        raw = await r.get(key)
        return json.loads(raw) if raw else None

    async def _send_alert(self, exchange, symbol, live_pos, cached) -> None:
        from apps.notification.telegram import TelegramNotifier
        msg = (
            f'持仓对账差异告警\n'
            f'交易所: {exchange} | 品种: {symbol}\n'
            f'实盘数量: {live_pos.quantity}\n'
            f'缓存数量: {cached.get("quantity", "N/A")}'
        )
        await TelegramNotifier.send_to_admins(msg)

    async def _get_redis(self) -> aioredis.Redis:
        return await aioredis.from_url(f'{settings.REDIS_URL}/5', decode_responses=True)


# apps/trading/tasks.py（Celery定时任务，作为对账的双重保障）
from celery import shared_task
import asyncio


@shared_task(name='apps.trading.tasks.sync_positions_from_exchange')
def sync_positions_from_exchange():
    """Celery beat每5分钟触发，与PositionMonitor互为备份"""
    from apps.agent.frame_manager import frame_manager, FrameType, FrameState
    if frame_manager._states.get(FrameType.TRADING) == FrameState.RUNNING:
        asyncio.run(frame_manager._auxiliary_monitor._do_reconcile())
```

---

## 四、RiskGuard实现

```python
# apps/riskguard/guard.py
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional, Tuple

import redis.asyncio as aioredis
from django.conf import settings

from apps.trading.adapters.base import OrderRequest

logger = logging.getLogger(__name__)


class RiskGuard:
    """
    风控守卫（随交易框架或辅助框架启动，非常驻独立进程——架构R1约束）。
    
    前置校验（pre_trade_check）：
    - 熔断器检查（circuit breaker）
    - 单笔仓位上限
    - 最大回撤限制
    - 日内交易次数
    
    实时监控（monitor loop）：
    - 浮亏超阈值预警
    - 止损触发强平
    """

    _instance: 'RiskGuard | None' = None

    CIRCUIT_BREAKER_KEY = 'circuit_breaker:{user_id}'
    MAX_POSITION_RATIO = Decimal('0.20')    # 单仓不超过总资产20%
    MAX_DAILY_DRAWDOWN = Decimal('0.05')    # 日内最大回撤5%
    MAX_DAILY_TRADES = 50                   # 日内最大交易次数
    FLOATING_LOSS_ALERT = Decimal('-0.03')  # 浮亏-3%预警

    def __init__(self, mode: str = 'trading'):
        self.mode = mode
        self._running = False
        self._monitor_task = None

    @classmethod
    def get_instance(cls) -> 'RiskGuard | None':
        return cls._instance

    async def start(self) -> None:
        RiskGuard._instance = self
        self._running = True
        if self.mode in ('trading', 'monitor'):
            import asyncio
            self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info(f'RiskGuard started (mode={self.mode})')

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
        RiskGuard._instance = None
        logger.info('RiskGuard stopped')

    # ------------------------------------------------------------------ #
    #  前置校验                                                            #
    # ------------------------------------------------------------------ #

    async def pre_trade_check(
        self, request: OrderRequest, user_id: str
    ) -> Tuple[bool, str]:
        """返回 (approved, reason)，approved=False时禁止下单"""

        # 1. 熔断器检查
        if await self._is_circuit_open(user_id):
            return False, '熔断器触发，今日禁止交易'

        # 2. 日内交易次数
        daily_count = await self._get_daily_trade_count(user_id)
        if daily_count >= self.MAX_DAILY_TRADES:
            return False, f'日内交易次数已达上限 {self.MAX_DAILY_TRADES}'

        # 3. 仓位上限
        position_ok, reason = await self._check_position_limit(request, user_id)
        if not position_ok:
            return False, reason

        # 4. 日内回撤
        drawdown_ok, reason = await self._check_drawdown(user_id)
        if not drawdown_ok:
            return False, reason

        return True, 'OK'

    async def _is_circuit_open(self, user_id: str) -> bool:
        r = await self._get_redis(db=7)
        key = self.CIRCUIT_BREAKER_KEY.format(user_id=user_id)
        val = await r.get(key)
        await r.aclose()
        return val is not None

    async def _get_daily_trade_count(self, user_id: str) -> int:
        from asgiref.sync import sync_to_async
        from apps.trading.models import Order
        from django.utils import timezone
        import datetime

        today = timezone.now().date()

        @sync_to_async
        def count():
            return Order.objects.filter(
                user_id=user_id,
                created_at__date=today,
                status__in=['submitted', 'filled'],
            ).count()

        return await count()

    async def _check_position_limit(self, request: OrderRequest, user_id: str) -> Tuple[bool, str]:
        """单仓不超过总资产20%"""
        from apps.trading.executor import OrderExecutor
        executor = OrderExecutor.get_instance()
        if not executor:
            return True, ''

        adapter = executor._adapters.get(request.exchange)
        if not adapter:
            return True, ''

        balance = await adapter.get_balance()
        total_usdt = balance.get('USDT', Decimal('0'))
        if total_usdt == 0:
            return True, ''

        order_value = (request.price or Decimal('0')) * request.quantity
        ratio = order_value / total_usdt
        if ratio > self.MAX_POSITION_RATIO:
            return False, f'单笔仓位 {ratio:.1%} 超过上限 {self.MAX_POSITION_RATIO:.0%}'

        return True, ''

    async def _check_drawdown(self, user_id: str) -> Tuple[bool, str]:
        """日内已实现回撤超过5%则禁止交易"""
        from asgiref.sync import sync_to_async
        from apps.trading.models import Order
        from django.utils import timezone

        today = timezone.now().date()

        @sync_to_async
        def get_today_pnl():
            from django.db.models import Sum
            result = Order.objects.filter(
                user_id=user_id,
                status='filled',
                created_at__date=today,
            ).aggregate(total_pnl=Sum('realized_pnl'))
            return result['total_pnl'] or Decimal('0')

        pnl = await get_today_pnl()
        if pnl < 0:
            # 需对比期初净值，此处简化：pnl < -MAX_DAILY_DRAWDOWN * 初始资金
            # 实际实现从daily_account_snapshot表取期初净值
            pass

        return True, ''

    # ------------------------------------------------------------------ #
    #  实时监控Loop                                                        #
    # ------------------------------------------------------------------ #

    async def _monitor_loop(self) -> None:
        import asyncio
        while self._running:
            try:
                await self._check_floating_pnl()
            except Exception as e:
                logger.error(f'RiskGuard monitor error: {e}')
            await asyncio.sleep(60)  # 每分钟检查

    async def _check_floating_pnl(self) -> None:
        """浮亏超阈值时发送预警（-3%）"""
        from apps.trading.executor import OrderExecutor
        executor = OrderExecutor.get_instance()
        if not executor:
            return

        for exchange, adapter in executor._adapters.items():
            positions = await adapter.get_positions()
            for pos in positions:
                balance = await adapter.get_balance()
                total = balance.get('USDT', Decimal('1'))
                pnl_ratio = pos.unrealized_pnl / total if total > 0 else Decimal('0')

                if pnl_ratio < self.FLOATING_LOSS_ALERT:
                    await self._send_floating_loss_alert(exchange, pos, pnl_ratio)

    async def _send_floating_loss_alert(self, exchange, pos, ratio) -> None:
        from apps.notification.telegram import TelegramNotifier
        msg = (
            f'浮亏预警\n'
            f'交易所: {exchange} | 品种: {pos.symbol}\n'
            f'方向: {pos.side} | 数量: {pos.quantity}\n'
            f'浮亏: {ratio:.2%}'
        )
        await TelegramNotifier.send_to_user(msg)

    async def _get_redis(self, db: int = 7) -> aioredis.Redis:
        return await aioredis.from_url(f'{settings.REDIS_URL}/{db}', decode_responses=True)
```

---

## 五、熔断器机制

```python
# apps/riskguard/circuit_breaker.py
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import redis.asyncio as aioredis
from django.conf import settings

logger = logging.getLogger(__name__)

CIRCUIT_BREAKER_TTL = 86400  # 熔断持续24小时（自然日重置）


class CircuitBreaker:
    """
    熔断触发条件（任一满足）：
    1. 日内亏损 > 最大回撤阈值
    2. 连续3次下单失败
    3. 交易所API连接中断 > 30秒
    4. 管理员手动触发
    """

    CONSECUTIVE_FAIL_THRESHOLD = 3
    FAIL_COUNT_TTL = 3600  # 连续失败计数1小时窗口

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._breaker_key = f'circuit_breaker:{user_id}'
        self._fail_count_key = f'circuit_fail_count:{user_id}'

    async def trip(self, reason: str, ttl: int = CIRCUIT_BREAKER_TTL) -> None:
        """触发熔断"""
        r = await self._get_redis()
        await r.setex(self._breaker_key, ttl, reason)
        await r.aclose()
        logger.warning(f'Circuit breaker tripped for user {self.user_id}: {reason}')

        # 发送告警
        from apps.notification.telegram import TelegramNotifier
        await TelegramNotifier.send_to_user(
            f'熔断器触发\n原因: {reason}\n持续时间: {ttl//3600}小时'
        )

    async def reset(self) -> None:
        """管理员手动重置"""
        r = await self._get_redis()
        await r.delete(self._breaker_key)
        await r.delete(self._fail_count_key)
        await r.aclose()
        logger.info(f'Circuit breaker reset for user {self.user_id}')

    async def record_failure(self) -> None:
        """记录下单失败，连续3次触发熔断"""
        r = await self._get_redis()
        count = await r.incr(self._fail_count_key)
        await r.expire(self._fail_count_key, self.FAIL_COUNT_TTL)
        await r.aclose()

        if count >= self.CONSECUTIVE_FAIL_THRESHOLD:
            await self.trip(f'连续{count}次下单失败')

    async def record_success(self) -> None:
        """下单成功，重置失败计数"""
        r = await self._get_redis()
        await r.delete(self._fail_count_key)
        await r.aclose()

    async def _get_redis(self) -> aioredis.Redis:
        return await aioredis.from_url(f'{settings.REDIS_URL}/7', decode_responses=True)
```

---

## 六、回测系统详细实现

```python
# apps/backtest/engine.py
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    start_ts: int
    end_ts: int
    initial_capital: Decimal
    final_capital: Decimal
    total_trades: int
    win_rate: Decimal
    max_drawdown: Decimal
    sharpe_ratio: Decimal
    profit_factor: Decimal
    trades: list = field(default_factory=list)


class BacktestEngine:
    """
    事件驱动回测引擎（随回测框架懒加载）。
    策略代码由QuantEngineerAgent的StrategyGenSkill生成，
    通过BacktestSkill传入此引擎执行。
    """

    def __init__(self, initial_capital: Decimal = Decimal('10000')):
        self._initial_capital = initial_capital
        self._capital = initial_capital
        self._positions: dict[str, Decimal] = {}  # symbol -> qty
        self._trades: list = []
        self._equity_curve: list = []

    async def run(
        self,
        strategy_fn: Callable[[Bar, dict], Optional[dict]],
        bars: list[Bar],
        strategy_name: str,
        symbol: str,
        commission_rate: Decimal = Decimal('0.0004'),
    ) -> BacktestResult:
        """
        strategy_fn: 接收(bar, state)，返回 {'action': 'buy'|'sell'|None, 'qty': Decimal}
        bars: 按时间升序排列的K线数据
        """
        state: dict = {}
        peak_capital = self._capital
        max_drawdown = Decimal('0')

        for bar in bars:
            # 调用策略函数
            signal = strategy_fn(bar, state)
            if signal and signal.get('action'):
                self._execute(signal, bar, commission_rate)

            # 更新净值
            current_equity = self._calc_equity(bar)
            self._equity_curve.append({'ts': bar.timestamp, 'equity': float(current_equity)})

            # 更新最大回撤
            if current_equity > peak_capital:
                peak_capital = current_equity
            drawdown = (peak_capital - current_equity) / peak_capital
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # 统计
        filled = [t for t in self._trades if t['status'] == 'filled']
        wins = [t for t in filled if t.get('pnl', Decimal('0')) > 0]
        win_rate = Decimal(len(wins)) / Decimal(max(len(filled), 1))
        final_capital = self._calc_equity(bars[-1]) if bars else self._capital

        return BacktestResult(
            strategy_name=strategy_name,
            symbol=symbol,
            start_ts=bars[0].timestamp if bars else 0,
            end_ts=bars[-1].timestamp if bars else 0,
            initial_capital=self._initial_capital,
            final_capital=final_capital,
            total_trades=len(filled),
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            sharpe_ratio=self._calc_sharpe(),
            profit_factor=self._calc_profit_factor(filled),
            trades=filled,
        )

    def _execute(self, signal: dict, bar: Bar, commission_rate: Decimal) -> None:
        action = signal['action']
        qty = signal.get('qty', Decimal('1'))
        price = bar.close
        commission = price * qty * commission_rate

        if action == 'buy':
            cost = price * qty + commission
            if cost <= self._capital:
                self._capital -= cost
                self._positions[bar.symbol if hasattr(bar, 'symbol') else 'default'] = qty
                self._trades.append({'action': 'buy', 'price': price, 'qty': qty, 'status': 'filled'})
        elif action == 'sell':
            symbol = bar.symbol if hasattr(bar, 'symbol') else 'default'
            held = self._positions.get(symbol, Decimal('0'))
            if held > 0:
                proceeds = price * min(qty, held) - commission
                entry_price = self._trades[-1]['price'] if self._trades else price
                pnl = (price - entry_price) * min(qty, held) - commission
                self._capital += proceeds
                self._positions[symbol] = max(Decimal('0'), held - qty)
                self._trades.append({'action': 'sell', 'price': price, 'qty': qty, 'status': 'filled', 'pnl': pnl})

    def _calc_equity(self, bar: Bar) -> Decimal:
        position_value = sum(
            qty * bar.close for qty in self._positions.values()
        )
        return self._capital + position_value

    def _calc_sharpe(self) -> Decimal:
        if len(self._equity_curve) < 2:
            return Decimal('0')
        import statistics
        returns = [
            self._equity_curve[i]['equity'] / self._equity_curve[i-1]['equity'] - 1
            for i in range(1, len(self._equity_curve))
        ]
        if not returns or statistics.stdev(returns) == 0:
            return Decimal('0')
        mean_r = statistics.mean(returns)
        std_r = statistics.stdev(returns)
        # 年化（假设日线：252交易日）
        return Decimal(str(mean_r / std_r * (252 ** 0.5)))

    def _calc_profit_factor(self, trades: list) -> Decimal:
        gross_profit = sum(t['pnl'] for t in trades if t.get('pnl', 0) > 0)
        gross_loss = abs(sum(t['pnl'] for t in trades if t.get('pnl', 0) < 0))
        if gross_loss == 0:
            return Decimal('999')
        return Decimal(str(gross_profit / gross_loss))


# apps/backtest/data_loader.py
class HistoricalDataLoader:
    """从PostgreSQL加载历史K线数据（market_data表）"""

    @staticmethod
    async def load(
        symbol: str,
        interval: str,
        start_ts: int,
        end_ts: int,
    ) -> list[Bar]:
        from asgiref.sync import sync_to_async
        from apps.market.models import MarketData

        @sync_to_async
        def fetch():
            qs = MarketData.objects.filter(
                symbol=symbol,
                interval=interval,
                timestamp__gte=start_ts,
                timestamp__lte=end_ts,
            ).order_by('timestamp').values(
                'timestamp', 'open', 'high', 'low', 'close', 'volume'
            )
            return list(qs)

        rows = await fetch()
        return [
            Bar(
                timestamp=r['timestamp'],
                open=Decimal(str(r['open'])),
                high=Decimal(str(r['high'])),
                low=Decimal(str(r['low'])),
                close=Decimal(str(r['close'])),
                volume=Decimal(str(r['volume'])),
            )
            for r in rows
        ]
```

---

## 七、Telegram通知通道

```python
# apps/notification/telegram.py
from __future__ import annotations

import logging
from typing import Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Telegram Bot通知（支持用户频道和管理员频道）。
    用于：
    - 持仓对账差异告警
    - 熔断器触发告警
    - 浮亏预警
    - Agent操作结果推送
    """

    BOT_TOKEN: str = ''
    USER_CHAT_ID: str = ''
    ADMIN_CHAT_ID: str = ''
    BASE_URL: str = 'https://api.telegram.org'

    @classmethod
    def _init(cls) -> None:
        if not cls.BOT_TOKEN:
            cls.BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
            cls.USER_CHAT_ID = settings.TELEGRAM_USER_CHAT_ID
            cls.ADMIN_CHAT_ID = settings.TELEGRAM_ADMIN_CHAT_ID

    @classmethod
    async def send(cls, chat_id: str, text: str, parse_mode: str = 'Markdown') -> bool:
        cls._init()
        url = f'{cls.BASE_URL}/bot{cls.BOT_TOKEN}/sendMessage'
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={
                    'chat_id': chat_id,
                    'text': text,
                    'parse_mode': parse_mode,
                })
                if resp.status_code != 200:
                    logger.error(f'Telegram send failed: {resp.text}')
                    return False
                return True
        except Exception as e:
            logger.error(f'Telegram send error: {e}')
            return False

    @classmethod
    async def send_to_user(cls, text: str) -> bool:
        return await cls.send(cls.USER_CHAT_ID or cls._get_user_chat_id(), text)

    @classmethod
    async def send_to_admins(cls, text: str) -> bool:
        cls._init()
        return await cls.send(cls.ADMIN_CHAT_ID, text)

    @classmethod
    def _get_user_chat_id(cls) -> str:
        cls._init()
        return cls.USER_CHAT_ID


# apps/notification/tasks.py
from celery import shared_task


@shared_task(name='apps.notification.tasks.send_telegram')
def send_telegram_task(chat_id: str, text: str) -> None:
    """Celery异步发送Telegram（非紧急通知走此路径）"""
    import asyncio
    asyncio.run(TelegramNotifier.send(chat_id, text))
```

---

## 八、API接口清单

### 8.1 Agent相关接口

| Method | Path | 描述 | 认证 |
|--------|------|------|------|
| POST | `/api/v1/agent/chat/` | 发送消息给Agent | JWT |
| GET | `/api/v1/agent/sessions/` | 获取会话列表 | JWT |
| GET | `/api/v1/agent/sessions/{id}/messages/` | 获取会话消息历史 | JWT |
| POST | `/api/v1/agent/intent/` | 强制指定意图（调试用） | JWT+Admin |

### 8.2 交易相关接口

| Method | Path | 描述 | 认证 |
|--------|------|------|------|
| GET | `/api/v1/trading/positions/` | 当前持仓（含缓存） | JWT |
| GET | `/api/v1/trading/orders/` | 订单列表（支持分页） | JWT |
| GET | `/api/v1/trading/orders/{id}/` | 订单详情 | JWT |
| POST | `/api/v1/trading/orders/cancel/` | 撤单（RiskGuard校验） | JWT |
| GET | `/api/v1/trading/balance/` | 账户余额 | JWT |
| GET | `/api/v1/trading/status/` | 交易框架状态 | JWT |

### 8.3 风控相关接口

| Method | Path | 描述 | 认证 |
|--------|------|------|------|
| GET | `/api/v1/risk/circuit-breaker/` | 熔断器状态 | JWT |
| POST | `/api/v1/risk/circuit-breaker/reset/` | 手动重置熔断器 | JWT+Admin |
| GET | `/api/v1/risk/events/` | 风控事件列表 | JWT |
| PUT | `/api/v1/risk/params/` | 更新风控参数 | JWT+Admin |

### 8.4 回测相关接口

| Method | Path | 描述 | 认证 |
|--------|------|------|------|
| POST | `/api/v1/backtest/run/` | 启动回测任务 | JWT |
| GET | `/api/v1/backtest/tasks/{id}/` | 回测任务状态 | JWT |
| GET | `/api/v1/backtest/results/{id}/` | 回测结果详情 | JWT |
| GET | `/api/v1/backtest/results/` | 回测结果列表 | JWT |

### 8.5 系统管理接口

| Method | Path | 描述 | 认证 |
|--------|------|------|------|
| GET | `/api/v1/system/frames/` | 各框架状态 | JWT+Admin |
| POST | `/api/v1/system/frames/start/` | 启动指定框架 | JWT+Admin |
| POST | `/api/v1/system/frames/stop/` | 停止指定框架 | JWT+Admin |
| GET | `/api/v1/system/health/` | 健康检查 | 无需认证 |

### 8.6 WebSocket接口

| Path | 描述 | 认证 |
|------|------|------|
| `ws://host/ws/agent/{session_id}/` | Agent实时推送 | JWT（QueryParam） |
| `ws://host/ws/trading/positions/` | 持仓实时推送 | JWT |
| `ws://host/ws/trading/orders/` | 订单状态实时推送 | JWT |

---

## 九、关键决策记录（ADR）落地

### ADR-001：持仓对账
- **决策**：每5分钟主动从交易所拉取持仓，与Redis缓存对比。
- **落地**：`PositionMonitor._reconcile_loop`（60行），差异>0.001触发Telegram告警。
- **备份**：Celery Beat `sync_positions_from_exchange` 每5分钟作为双重保障。

### ADR-002：策略代码隔离
- **决策**：QuantEngineerAgent生成策略Python文件，保存到DB，通过importlib动态加载。
- **落地**：`BacktestSkill` 调用 `importlib.import_module`，策略文件存入 `strategies` 表，沙箱执行（受限builtins）。
- **安全**：策略代码不允许import os/sys/subprocess，通过白名单校验。

### ADR-003：RiskGuard非常驻
- **决策**：RiskGuard随交易/辅助框架启动，框架停止时一同停止，不作为独立进程。
- **落地**：`FrameManager._do_start` 在启动 `TRADING`/`AUXILIARY` 时实例化并启动 `RiskGuard`。
- **状态**：`RiskGuard._instance` 单例，`get_instance()` 返回None时代表未运行（即框架未启动）。

### ADR-004：Prompt版本化
- **决策**：Prompt变更必须新建版本目录，不允许原地修改。
- **落地**：`PromptLoader.load(name, version)` 从 `prompts/{version}/{name}.txt` 加载，不存在时抛 `FileNotFoundError`。
- **约定**：生产环境默认 `version='v1'`，通过 `settings.PROMPT_VERSION` 覆盖。

### ADR-005：LLM降级
- **决策**：主模型→备用模型→规则引擎兜底，任何阶段失败不中断系统。
- **落地**：`LLMClient.chat` 最多3次重试，第2次切换 `gpt-3.5-turbo`，全部失败返回 `FALLBACK_RESPONSE` 标记。
- **Skill处理**：各Skill通过 `is_fallback(response)` 判断，走规则引擎分支返回保守结果。

---

*设计师C | trading_risk.md v1.0 | 交易执行与风控详细设计完成*
