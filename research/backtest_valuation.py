"""Cost-aware liquidation valuation for deterministic virtual strategy research."""

from __future__ import annotations

import math


def mark_to_liquidation_equity(
    cash: float,
    quantity: float,
    mark_price: float,
    adverse_exit_fraction: float,
    fee_rate: float,
) -> float:
    """Value an open spot position net of modeled exit impact and fee.

    This is a conservative liquidation-equity mark, not an actual trade fill.
    The adverse exit fraction already combines slippage and half-spread, matching
    the research simulator's SELL price model.
    """
    values = (cash, quantity, mark_price, adverse_exit_fraction, fee_rate)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in values
    ):
        raise ValueError("liquidation valuation inputs must be finite numbers")

    cash_value, qty_value, price_value, adverse_value, fee_value = map(float, values)
    if cash_value < 0 or qty_value < 0:
        raise ValueError("cash and position quantity must not be negative")
    if not 0 <= adverse_value < 1:
        raise ValueError("adverse exit fraction must be in [0, 1)")
    if not 0 <= fee_value < 1:
        raise ValueError("exit fee rate must be in [0, 1)")
    if qty_value == 0:
        return cash_value
    if price_value <= 0:
        raise ValueError("an open position requires a positive mark price")

    net_exit_price = price_value * (1.0 - adverse_value)
    net_exit_value = qty_value * net_exit_price * (1.0 - fee_value)
    return cash_value + net_exit_value
