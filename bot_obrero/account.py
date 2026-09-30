from dataclasses import dataclass
from decimal import Decimal

class ConfigurationMismatch(Exception): pass

@dataclass(frozen=True)
class AccountConfiguration:
    account_id: str
    position_mode: str
    margin_mode: str
    leverage: Decimal
    supported_order_flags: frozenset[str]=frozenset()
    symbol_rules: dict|None=None
    account_permissions: frozenset[str]=frozenset()

@dataclass(frozen=True)
class StrategyAssumptions:
    position_mode: str
    margin_mode: str
    leverage: Decimal
    required_order_flags: frozenset[str]=frozenset()

def validate_configuration(strategy, account):
    if (strategy.position_mode != account.position_mode or
        strategy.margin_mode != account.margin_mode or
        strategy.leverage != account.leverage or
        not strategy.required_order_flags.issubset(account.supported_order_flags)):
        raise ConfigurationMismatch("CONFIGURATION_MISMATCH")
