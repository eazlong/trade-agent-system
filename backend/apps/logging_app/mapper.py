"""
Module name mapper: maps Python logger names to system module identifiers.
"""
_MODULE_MAP = {
    'apps.agent': 'agent',
    'apps.trading': 'trading',
    'apps.riskguard': 'riskguard',
    'apps.signal_monitor': 'signal_monitor',
    'apps.memory': 'memory',
    'apps.channel': 'channel',
    'apps.notify': 'notify',
    'apps.exchange': 'exchange',
    'apps.datasource': 'datasource',
    'apps.authentication': 'auth',
    'apps.backtest': 'backtest',
    'apps.skill': 'skill',
    'apps.core': 'core',
    'core': 'core',
}


def resolve_module(logger_name: str) -> str:
    """Resolve a logger name to a module identifier."""
    for prefix, module in _MODULE_MAP.items():
        if logger_name.startswith(prefix):
            return module
    return 'unknown'
