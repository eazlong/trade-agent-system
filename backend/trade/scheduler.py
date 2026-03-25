# scheduler.py
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from django_apscheduler.jobstores import register_events, MemoryJobStore            
import assistant.fetch_data as fetch_data   
import json
from utils.redis_cache import redis_client
from django.utils import timezone
from datetime import timedelta, datetime
from trade.models import TbUserTradeRecord
from django.db.models import Sum
# from qtcore.trade_helper_manager import TradeHelperManger
from qtcore.binance_helper import BinanceHelper, BinanceAccountHelper
# from qtcore.okx_helper import OkxHelper, OkxAccountHelper
import time
from collections import defaultdict

def sync_orders():
    from utils.user_utils import get_all_user
    users = get_all_user()

    for user in users:
        try:
            helper = TradeHelperManger().helper(user.id)
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

def start():
    logger = logging.getLogger('apscheduler')
    logger.setLevel(logging.WARNING)
    scheduler = BackgroundScheduler()
  
    scheduler.add_jobstore(MemoryJobStore(), "assistant")

    # Schedule the calculate_profit function to run daily
    # fetch_common_data()
    scheduler.add_job(fetch_common_data, 'interval', minutes=15, id='fetch_common_data', replace_existing=True)

    register_events(scheduler)
    scheduler.start()
    logging.info("trade scheduler started...")