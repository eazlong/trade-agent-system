from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from trade.apis.serializers import TbUserTradingConfigSerializer
from trade.models import TbUserTradingConfig
from qtcore.trade_helper_manager import TradeHelperManager
from qtcore.binance_helper import BinanceTradeHelper, CommonBinanceHelper
from qtcore.okx_helper import CommonOkxHelper
from qtcore.symbol_helper import SymbolHelper
import logging
from qtcore.sqlite_helper import SQLiteHelper
import time

class TradeConfigViewSet(ViewSet):
    permission_classes = [IsAuthenticated]
    def list(self, request, pk=None):
        try:
            user = request.user
            trade_config = TbUserTradingConfig.objects.get(user_id=user.id)
            serializer = TbUserTradingConfigSerializer(trade_config)
            return Response(serializer.data)
        except TbUserTradingConfig.DoesNotExist:
            return Response({"error": "Trade configuration not found for user."}, status=404)

    def create(self, request):
        user = request.user

        data=request.data
        logging.info(data)
        data['user_id'] = user.id
        serializer = TbUserTradingConfigSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

    def update(self, request, pk=None):
        instance = TbUserTradingConfig.objects.get(id=pk)
        serializer = TbUserTradingConfigSerializer(instance, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)


class SymbolsViewSet(ViewSet):
    def list(self, request):        
        ss = CommonBinanceHelper().all_symbol()
        ss = list(set(filter(lambda x: x[-4:]=="USDT" and x[-5:]!=":USDT", ss)))
        return Response(ss, status=200)
    
    def retrieve(self, request, pk=None):
        symbol = pk
        ss = SymbolHelper().get_symbol_info(symbol)
        return Response(ss, status=200)
    
class KlinesViewSet(ViewSet):
    def _get_klines_from_cex(self, symbol, timeframe, start_time=None, end_time=None):
        try:
            try:
                raw_kl = CommonBinanceHelper().fetch_data(symbol, timeframe, limit=5000, since=int(start_time), end_time=int(end_time))
                if len(raw_kl) == 0:
                    raise Exception("Binance No data")
                logging.info(f"Query klines from Binance: {symbol} {timeframe} {raw_kl[-1][0]} {raw_kl[0][0]}")
            except Exception as e:
                pass
                # try:
                #     s = symbol.replace("USDT", "-USDT-SWAP")
                #     raw_kl = CommonOkxHelper().fetch_data(s, timeframe, limit=5000, since=int(start_time), end_time=int(end_time))
                #     if len(raw_kl) == 0:
                #         raise Exception("Okx No data")
                #     logging.info(f"Query klines from Okx: {symbol} {timeframe} {raw_kl[-1][0]} {raw_kl[0][0]}")
                # except Exception as e:
                #     # logging.exception(e)
                #     raise Exception("No data")
            data = [{
                'timestamp': int(k[0]),  # Convert milliseconds to seconds
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5])
            } for k in raw_kl]
            return data
        except Exception as e:
            logging.exception(f"Error fetching klines from CEX: {e}")
            return None

    def list(self, request, symbol, timeframe, start_time=None, end_time=None):
        raw_kl = SQLiteHelper().query_klines(symbol, timeframe, limit=5000, start_time=int(start_time), end_time=int(end_time))
        from_cex = False
        tf_in_seconds = int(timeframe[:-1]) * 60 if timeframe.endswith('m') else int(timeframe[:-1]) * 60 * 60 if timeframe.endswith('h') else int(timeframe[:-1]) * 60 * 60 * 24
        if (raw_kl and len(raw_kl['timestamp']) > 0 
            and end_time is not None \
            and raw_kl['timestamp'][-1] < int(end_time) - tf_in_seconds * 1000 \
            and int(end_time) < int(time.time())) \
            or (not raw_kl or len(raw_kl['timestamp']) == 0):
            raw_kl = self._get_klines_from_cex(symbol, timeframe, start_time=int(start_time), end_time=int(end_time))
            from_cex = True
            
        if raw_kl is None:
            return Response({"error": "No data"}, status=404)

        if from_cex:
            SQLiteHelper().save_kline(symbol, timeframe, {
                'timestamp': [kl['timestamp'] for kl in raw_kl],
                'open': [kl['open'] for kl in raw_kl],
                'high': [kl['high'] for kl in raw_kl],
                'low': [kl['low'] for kl in raw_kl],
                'close': [kl['close'] for kl in raw_kl],
                'volume': [kl['volume'] for kl in raw_kl]
            })
        else:
            raw_kl = [{
                'timestamp': raw_kl['timestamp'][i],
                'open': raw_kl['open'][i],
                'high': raw_kl['high'][i],
                'low': raw_kl['low'][i],
                'close': raw_kl['close'][i],
                'volume': raw_kl['volume'][i]
            } for i in range(len(raw_kl['timestamp']))]
        
        # raw_kl = self._get_klines_from_cex(symbol, timeframe, start_time=int(start_time), end_time=int(end_time))
        
        return Response(raw_kl, status=200)
    
class TradeViewSet(ViewSet):
    permission_classes = [IsAuthenticated]
    def create(self, request):
        user = request.user
        try:
            symbol = request.data.get('symbol')
            usdt = request.data.get('usdt')
            if usdt is None:
                trade_configs = TbUserTradingConfig.objects.filter(user_id=user.id)
                amount = trade_configs.small_amount
            else:
                amount = float(usdt)
    
            h = TradeHelperManager().helper(user.id)
            b = BinanceTradeHelper(symbol, h)
            amount = b.usdt2amount(amount)
            o = b.binance_market_buy(amount)
            logging.info(o['info'])
            price = float(o['info']['avgPrice'])
            b.binance_take_profit_limit(amount, price*1.1, price*1.1)
            b.binance_stop_limit(amount, price*0.98, price*0.98)
            return Response(o, status=201)
        except TbUserTradingConfig.DoesNotExist:
            return Response({"error": "Trade configuration not found for user."}, status=404)
        except ValueError:
            return Response({"error": "Invalid amount provided."}, status=400)
        except Exception as e:
            logging.error(f"An error occurred: {str(e)}")
            logging.exception(e)
            return Response({"error": f"{str(e)}"}, status=500)
        

