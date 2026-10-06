"""Minimal deterministic backtest metrics for FRANKESTAIN.

Architectural inspiration:
- Jesse backtest reporting
- Freqtrade backtesting/protections
- OctoBot backtesting result summaries

Original provider-neutral implementation. No persistence or plotting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class EquityPoint:
    timestamp: str
    equity: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("timestamp must be non-empty")
        if not isinstance(self.equity, Decimal) or not self.equity.is_finite():
            raise ValueError("equity must be finite Decimal")
        if self.equity <= 0:
            raise ValueError("equity must be greater than zero")


@dataclass(frozen=True)
class BacktestMetrics:
    initial_equity: Decimal
    final_equity: Decimal
    total_return: Decimal
    max_drawdown: Decimal
    trade_count: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal

    def __post_init__(self) -> None:
        if self.trade_count < 0:
            raise ValueError("trade_count cannot be negative")
        if self.winning_trades < 0 or self.losing_trades < 0:
            raise ValueError("winning/losing trades cannot be negative")
        if self.winning_trades + self.losing_trades > self.trade_count:
            raise ValueError("wins plus losses cannot exceed trade_count")


def summarize_equity_curve(
    points: Sequence[EquityPoint],
    *,
    trade_count: int = 0,
    winning_trades: int = 0,
    losing_trades: int = 0,
) -> BacktestMetrics:
    """Compute deterministic metrics from an already materialized equity curve."""

    values = tuple(points)
    if not values:
        raise ValueError("points must not be empty")

    initial = values[0].equity
    final = values[-1].equity

    peak = initial
    max_drawdown = Decimal("0")

    for point in values:
        if point.equity > peak:
            peak = point.equity
        drawdown = (point.equity / peak) - Decimal("1")
        if drawdown < max_drawdown:
            max_drawdown = drawdown

    total_return = (final / initial) - Decimal("1")

    if trade_count == 0:
        win_rate = Decimal("0")
    else:
        win_rate = Decimal(winning_trades) / Decimal(trade_count)

    return BacktestMetrics(
        initial_equity=initial,
        final_equity=final,
        total_return=total_return,
        max_drawdown=max_drawdown,
        trade_count=trade_count,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
    )


__all__ = [
    "BacktestMetrics",
    "EquityPoint",
    "summarize_equity_curve",
]
