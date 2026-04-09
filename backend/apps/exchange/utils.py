"""
Utility functions for exchange account management
"""

from typing import Optional
from apps.exchange.models import ExchangeAccount


def get_exchange_account(account_id: str) -> Optional[ExchangeAccount]:
    """
    获取交易所账户实例

    Args:
        account_id: 交易所账户UUID

    Returns:
        ExchangeAccount实例或None
    """
    try:
        return ExchangeAccount.objects.get(id=account_id, is_active=True)
    except ExchangeAccount.DoesNotExist:
        return None


def create_exchange_account(exchange: str, label: str, api_key: str, api_secret: str) -> ExchangeAccount:
    """
    创建新的交易所账户

    Args:
        exchange: 交易所名称 (binance, okx, bybit)
        label: 账户标签
        api_key: API密钥
        api_secret: API密钥密钥

    Returns:
        ExchangeAccount实例
    """
    account = ExchangeAccount(
        exchange=exchange,
        label=label
    )
    account.api_key_enc = account.encrypt_api_key(api_key)
    account.api_secret_enc = account.encrypt_api_secret(api_secret)
    account.save()
    return account


def verify_exchange_connection(account_id: str) -> bool:
    """
    验证交易所账户连接

    Args:
        account_id: 交易所账户UUID

    Returns:
        连接是否有效
    """
    account = get_exchange_account(account_id)
    if not account:
        return False

    try:
        # 这里可以根据具体交易所实现连接验证
        # 示例：使用ccxt库验证连接
        import ccxt
        exchange_class = getattr(ccxt, account.exchange)()
        exchange_class.apiKey = account.decrypt_api_key()
        exchange_class.secret = account.decrypt_api_secret()

        # 尝试获取账户信息验证连接
        balance = exchange_class.fetch_balance()
        return True
    except Exception:
        return False