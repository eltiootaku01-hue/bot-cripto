from dataclasses import dataclass, field
from decimal import Decimal

class ConfigurationMismatch(Exception):
    """Strategy/account assumptions are not compatible."""
    pass

@dataclass(frozen=True)
class AccountConfiguration:
    account_id: str
    position_mode: str
    margin_mode: str
    leverage: Decimal
    supported_order_flags: frozenset[str] = frozenset()
    symbol_rules: dict = field(default_factory=dict)
    account_permissions: frozenset[str] = frozenset()
    supported_sides: frozenset[str] = frozenset({"BUY", "SELL"})

@dataclass(frozen=True)
class StrategyAssumptions:
    position_mode: str
    margin_mode: str
    leverage: Decimal
    required_order_flags: frozenset[str] = frozenset()
    side: str | None = None
    required_permissions: frozenset[str] = frozenset()
    symbol: str | None = None

def validate_configuration(strategy: StrategyAssumptions, account: AccountConfiguration):
    mismatches = []
    if strategy.position_mode != account.position_mode:
        mismatches.append("position_mode")
    if strategy.margin_mode != account.margin_mode:
        mismatches.append("margin_mode")
    if strategy.leverage != account.leverage:
        mismatches.append("leverage")
    if not strategy.required_order_flags.issubset(account.supported_order_flags):
        mismatches.append("order_flags")
    if strategy.side is not None and strategy.side not in account.supported_sides:
        mismatches.append("side")
    if not strategy.required_permissions.issubset(account.account_permissions):
        mismatches.append("permissions")
    if strategy.symbol is not None and strategy.symbol not in account.symbol_rules:
        mismatches.append("symbol_rules")
    if mismatches:
        raise ConfigurationMismatch("CONFIGURATION_MISMATCH:" + ",".join(mismatches))
    return True
