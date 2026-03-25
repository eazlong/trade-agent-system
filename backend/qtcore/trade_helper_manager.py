from qtcore.binance_helper import BinanceHelper
from qtcore.okx_helper import OkxHelper
from utils.singleton import singleton
from trade.models import TbUserTradingConfig
from ccxt.base.errors import AuthenticationError
import logging

@singleton
class TradeHelperManager():
    _user_helpers = {}

    def helper(self, user_id, exchange_type=None):
        # If exchange_type is not provided, get from user config
        if user_id not in self._user_helpers or exchange_type:
            try:
                config = TbUserTradingConfig.objects.get(user_id=user_id)
                
                # If exchange_type is not specified, use the one from config
                if not exchange_type:
                    exchange_type = config.exchange_type if hasattr(config, 'exchange_type') else "binance"
                    
                # Create the appropriate helper based on exchange type
                if exchange_type.lower() == "okx":                    
                    self._user_helpers[user_id] = OkxHelper(
                        config.key, 
                        config.secret, 
                        config.password,
                        sandbox=getattr(config, 'is_sandbox', False)
                    )
                else:
                    # Default to Binance
                    self._user_helpers[user_id] = BinanceHelper(
                        config.key, 
                        config.secret, 
                        sandbox=getattr(config, 'is_sandbox', False)
                    )
            except TbUserTradingConfig.DoesNotExist:
                # logging.error(f"No trade config found for user {user_id}")
                return None
            except AuthenticationError:
                logging.error(f"Authentication error for user {user_id}")
                return None
            except Exception as e:
                logging.exception(f"Error creating exchange helper for user {user_id}: {str(e)}")
                return None
                
        return self._user_helpers[user_id]
    