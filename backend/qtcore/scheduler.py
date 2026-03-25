# scheduler.py
import logging
from re import L
from apscheduler.schedulers.background import BackgroundScheduler
from django_apscheduler.jobstores import DjangoJobStore, register_events, MemoryJobStore
from django.utils import timezone
from datetime import timedelta, datetime
from trade.models import TbUserTradeRecord
from qtbot.models import TbUserBotConfig
from django.db.models import Sum
from qtcore.trade_helper_manager import TradeHelperManager
from qtcore.binance_helper import BinanceHelper, BinanceAccountHelper
from qtcore.okx_helper import OkxHelper, OkxAccountHelper
import time
from collections import defaultdict

def calculate_bot_profit():
    try:
        # Filter records by date range
        trade_records = TbUserTradeRecord.objects.all()#filter(timestamp__range=(since, timezone.now()))
        
        # Group by userid and botname, and calculate the sum of profits
        profit_summary = trade_records.values('user_id', 'bot_id').annotate(total_profit=Sum('profit'))
        
        # Update the profit field in TbUserBot
        for record in profit_summary:
            try:
                TbUserBotConfig.objects.filter(id=record['bot_id'], deleted=False).update(profit=record['total_profit'])
            except Exception as e:
                pass
        
        # Return the total profit calculated (optional)
        return sum(record['total_profit'] for record in profit_summary)
    
    except Exception as e:
        # logging.error(f"Error calculating profit: {str(e)}")
        return 0

def calculate_human_profit():
    try:
        from django.db.models import F, Q, ExpressionWrapper, FloatField, Case, When, Value

        # Annotate每条记录，平单时amount为负，开单时为正
        annotated_records = TbUserTradeRecord.objects.filter(bot_id=-1).annotate(
            signed_amount=Case(
                When(Q(action='sell') & Q(side='LONG'), then=F('amount')),
                When(Q(action='sell') & Q(side='SHORT'), then=F('amount')),
                default=ExpressionWrapper(-1 * F('amount'), output_field=FloatField()),
                output_field=FloatField()
            ),
            amount_price=ExpressionWrapper(
                F('signed_amount') * F('price'),
                output_field=FloatField()
            )
        )

        # 按parent_order_id分组，计算每组的amount*price之和，排除parent_order_id为空的记录
        trade_sums = annotated_records.filter(bot_id=-1).values('parent_order_id').annotate(
            total_amount_price=Sum('amount_price')
        )

        logging.debug(f"trade_sums: {trade_sums}")
        for record in trade_sums:
            first = TbUserTradeRecord.objects.filter(parent_order_id=record['parent_order_id']).order_by('-timestamp').first()
            if first.unfilled_amount < 1e-9:
                TbUserTradeRecord.objects.filter(order_id=record['parent_order_id']).update(profit=record['total_amount_price'])
                # logging.info(f"calculate_human_profit: {record['parent_order_id']} {record['total_amount_price']}")
    except Exception as e:
        logging.error(f"Error calculating human profit: {str(e)}")
        pass

def sync_orders():
    from utils.user_utils import get_all_user
    users = get_all_user()

    for user in users:
        try:
            helper = TradeHelperManager().helper(user.id)
            if isinstance(helper, BinanceHelper):
                account = BinanceAccountHelper(helper)
            elif isinstance(helper, OkxHelper):
                account = OkxAccountHelper(helper)
            else:
                continue

            all_new_trades = []
            if isinstance(helper, OkxHelper):
                # For OKX, fetch all trades in a single efficient call
                last_record = TbUserTradeRecord.objects.filter(user_id=user.id, bot_id=-1).order_by('-timestamp').first()
                since = int(last_record.timestamp.timestamp() * 1000) if last_record else 0
                all_new_trades = account.fetch_user_trades(since=since)
            elif isinstance(helper, BinanceHelper):
                # For Binance, get active symbols to avoid hitting rate limits
                # 1. Get symbols with open positions
                income_history = account.get_income_history()
                active_symbols = set([p['symbol'] for p in income_history])
                
                # 2. Get symbols with previously unfilled trades from our DB
                # unfilled_symbols = set(TbUserTradeRecord.objects.filter(user_id=user.id, bot_id=-1, unfilled_amount__gt=0).values_list('symbol', flat=True))
                # symbols_to_check = active_symbols.union(unfilled_symbols)

                for symbol in active_symbols:
                    last_record = TbUserTradeRecord.objects.filter(user_id=user.id, bot_id=-1, symbol=symbol).order_by('-timestamp').first()
                    since = int((last_record.timestamp.timestamp() + 1) * 1000) if last_record else 0
                    trades = account.all_orders(symbol=symbol, since=since)
                    trades = [t for t in trades if t['info']['status'] == 'FILLED']
                    all_new_trades.extend(list(trades))

            logging.debug(f"all_new_trades: {all_new_trades}")
            # Filter out trades that might already be in the database
            existing_order_ids = set(TbUserTradeRecord.objects.filter(user_id=user.id, bot_id=-1, order_id__in=[str(t['id']) for t in all_new_trades]).values_list('order_id', flat=True))
            new_trades = [t for t in all_new_trades if str(t['id']) not in existing_order_ids]
            logging.debug(f"new_trades: {new_trades}")

            # Group trades by symbol for processing
            trades_by_symbol = defaultdict(list)
            for trade in new_trades:
                trades_by_symbol[trade['info']['symbol']].append(trade)

            for symbol, trades in trades_by_symbol.items():
                # Sort trades by timestamp to process them in order
                trades.sort(key=lambda x: x['timestamp'])

                parent_order_id = None
                first_buy_order_ids = None
                unfilled_amount = 0.0

                # Get the last known state for this specific symbol
                last_symbol_record = TbUserTradeRecord.objects.filter(user_id=user.id, bot_id=-1, symbol=symbol).order_by('-timestamp').first()
                if last_symbol_record:
                    unfilled_amount = round(float(last_symbol_record.unfilled_amount), 4)
                    if unfilled_amount > 1e-8:
                        first_buy_order_ids = last_symbol_record.parent_order_id
                
                for order in trades:
                    try:
                        action=order['side'].upper()
                        info = order['info']
                        side=(info.get('positionSide') or info.get('posSide', '')).upper()
                        if (action == "BUY" and side == "LONG") or (action == "SELL" and side == "SHORT"): #开仓
                            if first_buy_order_ids is None:
                                first_buy_order_ids = str(order['id'])
                            parent_order_id = first_buy_order_ids
                            unfilled_amount += round(float(order['amount']), 4)
                            logging.info(f"{user.username} {symbol}  {action} {side} - unfilled_amount: {unfilled_amount}, parent_order_id:{parent_order_id}, order_id: {str(order['id'])}")

                        elif (action == "SELL" and side == "LONG") or (action == "BUY" and side == "SHORT"): #平仓
                            parent_order_id = first_buy_order_ids
                            unfilled_amount -= round(float(order['amount']), 4)
                            if unfilled_amount <= 1e-9:
                                first_buy_order_ids = None
                            logging.info(f"{user.username} {symbol} {action} {side} - unfilled_amount: {unfilled_amount}, parent_order_id:{parent_order_id}, order_id: {str(order['id'])}")
                        
                        TbUserTradeRecord.objects.create(
                            symbol=symbol,
                            user_id=user.id,
                            bot_id=-1,
                            bot_name="human",
                            order_id=str(order['id']),
                            parent_order_id=parent_order_id,
                            unfilled_amount=unfilled_amount,
                            price=info['avgPrice'],
                            amount=info['executedQty'],
                            action=action.lower(),
                            side=side,
                            timestamp=timezone.make_aware(datetime.fromtimestamp(int(order['timestamp']) / 1000))
                        )
                        
                        time.sleep(0.1) # Reduced sleep time as we are not hitting the API in a loop
                    except Exception as e:
                        logging.error(f"Error syncing order for user {user.username}: {str(e)}")
                        pass
        except Exception as e:
            logging.error(f"Error syncing orders for user {user.username}: {str(e)}")
            # logging.exception(e )
            pass
            # logging.error(str(e))

def fetch_positions():
    from utils.user_utils import get_all_user
    users = get_all_user()

    for user in users:
        try:
            helper = TradeHelperManager().helper(user.id)
            if not helper:
                continue

            account = BinanceAccountHelper(helper)
            positions = account.position_risk()
            
            # 获取该用户所有未删除的bot配置
            active_bots = TbUserBotConfig.objects.filter(user_id=user.id, deleted=False)
            
            for position in positions:
                symbol = position['symbol']
                logging.info(f"fetch_positions {user.username} {symbol} {position}")
                # 查找匹配的bot配置
                matching_bots = active_bots.filter(symbol=symbol)
                
                if matching_bots.exists():
                    # 更新仓位和盈利信息
                    matching_bots.update(
                        position_amount=float(position['positionAmt']),
                        position_side=position['positionSide'],
                        unrealized_profit=float(position['unRealizedProfit']),
                        liquidation_price=float(position['liquidationPrice'])
                    )
                    active_bots = active_bots.exclude(id__in=matching_bots.values_list('id', flat=True))
                
            if len(active_bots) > 0:
                active_bots.update(position_amount=0.0,
                    position_side="",
                    unrealized_profit=0.0,
                    liquidation_price=0.0
                    )
        except Exception as e:
            logging.error(f"Error fetching positions for user {user.id}: {str(e)}")
            pass

def start():
    logger = logging.getLogger('apscheduler')
    logger.setLevel(logging.WARNING)
    scheduler = BackgroundScheduler()
    # Use a try-except block to handle database lock issues
    # try:
    #     scheduler.add_jobstore(DjangoJobStore(), "default")
    # except Exception as e:
    #     logging.error(f"Failed to add DjangoJobStore: {str(e)}")
    #     # Fallback to memory jobstore if database is locked
    # from apscheduler.jobstores.memory import MemoryJobStore
    scheduler.add_jobstore(MemoryJobStore(), "default")

    # Schedule the calculate_bot_profit function to run daily
    scheduler.add_job(calculate_bot_profit, 'interval', minutes=8, id='calculate_bot_profit', replace_existing=True)
    scheduler.add_job(calculate_human_profit, 'interval', minutes=60, id='calculate_human_profit', replace_existing=True)
    scheduler.add_job(sync_orders, 'interval', minutes=19, id='sync_orders', replace_existing=True)
    # scheduler.add_job(fetch_positions, 'interval', minutes=5, id='fetch_positions', replace_existing=True)

    register_events(scheduler)
    scheduler.start()
    logging.info("Scheduler started...")